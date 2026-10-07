"""
SQLOps Guardian - rewrite verification agent.

The problem this solves: analyze_sql asks an LLM for a rewritten query and
returns it, and nothing ever checks that the rewrite returns the same rows as
the original. That is not a hypothetical risk. This project shipped a seed
case whose "fix" used UNION ALL where the original semantics required UNION,
and it sat in the knowledge base being retrieved as a precedent until someone
read it closely (commit 71c7907). A rewrite that is wrong in that way is worse
than no rewrite, because it looks authoritative.

So: execute both queries against a fixture database and compare the results.

Two design choices are worth stating up front, because they are the whole
argument of this module.

1. The comparison is the oracle; the LLM is not.
   On a fixed dataset, comparing two result sets as multisets is *decisive*.
   If they differ, the rewrite is not equivalent -- that is a proof, and no
   model opinion can overturn it. So compare_results runs first,
   deterministically, before any network call. A mismatch short-circuits to
   not_equivalent and Groq is never contacted. This is not just an
   optimisation: it means the trustworthy half of the verdict space does not
   depend on an LLM at all, and the eval reports how many cases were settled
   this way.

   The LLM runs only on the remaining case -- results matched -- where the
   interesting question is no longer "are these equivalent on this data"
   (answered: yes) but "is this data strong enough for that match to mean
   anything". Deciding what evidence would separate two queries is a creative
   task, and it is the one thing a fixed comparison cannot do.

2. Executing the SQL breaks this project's standing invariant, so the
   invariant is rebuilt by containment.
   Everywhere else -- mcp_server, linter, llm_analyzer -- SQL is data and is
   never executed, which makes a hostile string inert. Here, executing it is
   the feature; equivalence cannot be checked any other way. And the path is
   fully untrusted end to end: user text -> LLM -> SQL we execute. A prompt
   injection inside a SQL comment is a plausible route to DROP TABLE. The
   controls, weakest to strongest:
     - sqlglot parse, single statement, SELECT-shaped, no DML/DDL node
       anywhere in the tree;
     - a read-only connection (mode=ro), so a write is refused by SQLite
       itself even if the parser were fooled;
     - a row limit and a wall-clock timeout, against resource exhaustion;
     - and the fixture database is disposable and holds nothing of value.
   The last one is the real boundary. The parser check is there so that a bug
   does not become a breach.

Verdicts are three-state on purpose. "equivalent" as a bare boolean would be
a lie: a match proves equivalence *on this data*, which is a statement about
the fixture, not about the queries. See Verdict below.
"""

import json
import logging
import sqlite3
import time
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from . import verify_fixture
from .config import config

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Result model
# --------------------------------------------------------------------------

class Verdict(str, Enum):
    """The three honest outcomes of a verification.

    NOT_EQUIVALENT is the only verdict that is ever a proof. It is reached by
    running both queries and observing different result multisets, which
    settles the matter without appeal.

    EQUIVALENT_ON_TEST_DATA is deliberately not called "equivalent". The
    results matched on the fixture and no distinguishing case was found; that
    is evidence, not proof. A rewrite that diverges only on an empty table, or
    only when some column happens to be entirely NULL, earns this verdict
    while still being wrong in production. The name carries that caveat so a
    caller cannot drop it by accident.

    UNDETERMINED means the process did not reach a conclusion: the step limit
    was hit, a query was rejected, or something errored. It is kept distinct
    from NOT_EQUIVALENT because "we could not check this" and "we proved this
    is wrong" call for completely different responses -- the same reason
    LayerStatus keeps SKIPPED apart from FAILED.
    """

    NOT_EQUIVALENT = "not_equivalent"
    EQUIVALENT_ON_TEST_DATA = "equivalent_on_test_data"
    UNDETERMINED = "undetermined"


class ToolCall(BaseModel):
    """One tool invocation and its outcome, for the audit trail.

    The agent knows nothing except what these calls returned, so this list is
    not logging decoration -- it *is* the reasoning trace, and it is the only
    way to tell a verdict that followed from evidence from one the model
    asserted.
    """

    step: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    # Truncated for readability; the full result went to the model.
    result: str = ""
    ok: bool = True


class VerificationResult(BaseModel):
    verdict: Verdict
    evidence: str
    steps_taken: int

    # True when the deterministic comparison settled it and Groq was never
    # called. The eval reports this count: it measures how much of the work
    # the oracle does on its own, which is the main claim this module makes.
    decided_without_llm: bool = False

    # How the LLM probing phase went, independent of the verdict. This is
    # reported separately because the two can diverge in a way that hides
    # breakage: when probing fails, the verdict correctly falls back to
    # EQUIVALENT_ON_TEST_DATA -- the results really did match -- and the run
    # looks successful. The first live eval scored 6/6 while the Groq
    # integration was in fact returning 400 on two pairs. "ok" means the agent
    # answered, "skipped" means no key, "failed" means it errored out.
    probe_status: str = "not_needed"
    probe_error: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class QueryRejected(Exception):
    """A candidate query failed the read-only guard. Never executed."""


# --------------------------------------------------------------------------
# The read-only guard
# --------------------------------------------------------------------------

# Rejected wherever they appear in the tree, not just at the root. exp.DDL and
# exp.DML are sqlglot's own base classes for statements that create/alter and
# that write rows; the rest are statements outside both that can still change
# state or reach the filesystem. exp.Command is sqlglot's catch-all for syntax
# it did not model (VACUUM parses to it) -- if the parser could not describe a
# statement, we cannot reason about it, so it is refused rather than trusted.
_FORBIDDEN_NODES = (
    exp.DDL,
    exp.DML,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Set,
    exp.Command,
)

# A set operation (UNION/EXCEPT/INTERSECT) has its own root class rather than
# being a Select, so both are allowed roots. exp.Subquery covers a parenthesised
# top-level select.
_ALLOWED_ROOTS = (exp.Select, exp.SetOperation, exp.Subquery)


def assert_read_only_select(sql: str) -> exp.Expression:
    """Parse `sql` and raise QueryRejected unless it is a single read-only SELECT.

    Returns the parsed expression so callers do not parse twice.

    The tree walk is the part that matters, and it is not paranoia. sqlglot
    parses

        WITH x AS (DELETE FROM orders RETURNING id) SELECT * FROM x

    with a root type of Select, so a check on the root alone accepts a
    statement that deletes every row in a table. Likewise `SELECT 1; DROP
    TABLE orders` is two statements, and `/*c*/ DELETE FROM orders` defeats any
    regex anchored on a leading SELECT. All three are refused below.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise QueryRejected("The query was empty. Pass a single SELECT statement.")

    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except Exception as e:
        raise QueryRejected(
            f"The query could not be parsed as SQLite SQL: {e}. It was not executed."
        ) from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise QueryRejected(
            f"Expected exactly one statement, found {len(statements)}. Batches are "
            "refused because only the first would be checked. Send one SELECT."
        )

    root = statements[0]
    if not isinstance(root, _ALLOWED_ROOTS):
        raise QueryRejected(
            f"Only SELECT statements can be run here; this parsed as "
            f"{type(root).__name__}. It was not executed."
        )

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise QueryRejected(
                f"The query contains a {type(node).__name__} node, which writes or "
                "changes state. Only read-only SELECTs can be run here. It was "
                "not executed."
            )

    return root


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def _connect_read_only(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the fixture read-only at the connection level.

    mode=ro is enforced by SQLite, below and independent of anything this
    module parses: any attempt to write raises "attempt to write a readonly
    database" even if the guard above were bypassed entirely. uri=True is what
    makes the query-string form meaningful -- without it the whole string is
    treated as a filename and the mode is silently ignored, so the two must
    stay together.
    """
    path = Path(db_path or config.VERIFY_DB_PATH)
    verify_fixture.ensure(path)
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def run_query(sql: str, db_path: str | Path | None = None) -> dict:
    """Execute one read-only SELECT against the fixture and return its rows.

    Raises QueryRejected if the statement is not a single read-only SELECT, if
    it exceeds the time budget, or if SQLite refuses it.

    The row limit is applied by fetching limit+1 rows rather than by appending
    LIMIT to the SQL. Rewriting a query to bound it would change the thing
    being measured -- a query that already carries its own LIMIT or sits under
    a set operation would end up meaning something different from what the
    caller asked about.
    """
    assert_read_only_select(sql)

    conn = _connect_read_only(db_path)
    limit = config.VERIFY_ROW_LIMIT
    deadline = time.monotonic() + config.VERIFY_TIMEOUT_MS / 1000
    timed_out = False

    def _interrupt_if_overdue() -> int:
        # Returning non-zero aborts the statement. A progress handler is used
        # rather than signal.alarm because this runs on Windows too, and
        # rather than sqlite3's `timeout` parameter, which bounds waiting for a
        # lock and not query execution.
        nonlocal timed_out
        if time.monotonic() > deadline:
            timed_out = True
            return 1
        return 0

    try:
        conn.set_progress_handler(_interrupt_if_overdue, 1000)
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(limit + 1)
        columns = [d[0] for d in cursor.description or []]
    except sqlite3.Error as e:
        if timed_out:
            raise QueryRejected(
                f"The query exceeded the {config.VERIFY_TIMEOUT_MS}ms time budget "
                "and was cancelled. Simplify it or add a narrower WHERE clause."
            ) from e
        raise QueryRejected(f"SQLite refused the query: {e}") from e
    finally:
        conn.set_progress_handler(None, 0)
        conn.close()

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    return {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


def get_schema(db_path: str | Path | None = None) -> dict:
    """Return the fixture's tables, columns, nullability and row counts.

    Nullability is included because it is frequently the whole question. An
    agent cannot reason about NOT IN versus NOT EXISTS, or COUNT(*) versus
    COUNT(col), without knowing which columns can be NULL.

    This reads sqlite_master and PRAGMA table_info directly rather than going
    through run_query, which would refuse a Pragma node. That is the correct
    asymmetry: the guard exists to constrain agent-supplied SQL, and these two
    statements are ours, fixed, and take no input.
    """
    conn = _connect_read_only(db_path)
    try:
        names = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = []
        for name in names:
            columns = [
                {
                    "name": row[1],
                    "type": row[2] or "ANY",
                    # PRAGMA table_info: notnull is column 3, pk is column 5.
                    "nullable": not row[3] and not row[5],
                }
                for row in conn.execute(f'PRAGMA table_info("{name}")')
            ]
            (count,) = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
            tables.append({"table": name, "row_count": count, "columns": columns})
    finally:
        conn.close()

    return {"tables": tables}


# --------------------------------------------------------------------------
# The oracle
# --------------------------------------------------------------------------

_DIFF_SAMPLE = 5


def compare_results(
    sql_a: str, sql_b: str, db_path: str | Path | None = None
) -> dict:
    """Run both queries and report whether their result sets match as multisets.

    Multisets, not sets: duplicates count. That distinction is the point. The
    classic wrong rewrite turns `WHERE EXISTS (SELECT ... FROM orders)` into a
    JOIN, which emits the left row once per matching right row. As sets the two
    results are identical; only counting duplicates reveals it. The same is
    true of UNION versus UNION ALL.

    Row order is ignored, since neither query is required to be ordered and SQL
    makes no promise about order without ORDER BY. Where order is itself the
    thing under test, that is visible as an ORDER BY difference in the SQL, not
    in the rows.

    Returns a dict with `match`, and when it is False a short diff: rows
    present in one side and not the other, with their multiplicities.
    `comparable` is False when either query could not be run at all, which is
    not the same as a mismatch.
    """
    out: dict[str, Any] = {"comparable": True, "match": False}

    try:
        a = run_query(sql_a, db_path)
    except QueryRejected as e:
        return {"comparable": False, "match": False,
                "error": f"The original query could not be run: {e}"}
    try:
        b = run_query(sql_b, db_path)
    except QueryRejected as e:
        return {"comparable": False, "match": False,
                "error": f"The rewritten query could not be run: {e}"}

    out["original"] = {"row_count": a["row_count"], "columns": a["columns"]}
    out["rewrite"] = {"row_count": b["row_count"], "columns": b["columns"]}

    # A truncated side makes the comparison unsound in the "match" direction:
    # the rows we did not fetch could differ. A mismatch within the rows we did
    # fetch is still a proof, so only report incomparable when they matched.
    truncated = a["truncated"] or b["truncated"]

    def multiset(rows: list[list]) -> dict[tuple, int]:
        counts: dict[tuple, int] = {}
        for row in rows:
            # repr() rather than the raw tuple: SQLite hands back floats, None
            # and bytes, and 1 == 1.0 == True would collapse distinct values
            # into one key.
            key = tuple(repr(v) for v in row)
            counts[key] = counts.get(key, 0) + 1
        return counts

    ca, cb = multiset(a["rows"]), multiset(b["rows"])

    if len(a["columns"]) != len(b["columns"]):
        out["match"] = False
        out["diff"] = (
            f"Different column counts: the original returns "
            f"{len(a['columns'])} ({', '.join(a['columns'])}) and the rewrite "
            f"{len(b['columns'])} ({', '.join(b['columns'])}). A rewrite that "
            "changes the shape of the result is not equivalent."
        )
        return out

    if ca == cb:
        if truncated:
            return {
                "comparable": False,
                "match": False,
                "error": (
                    f"Both result sets were truncated at the "
                    f"{config.VERIFY_ROW_LIMIT}-row limit and agreed on the rows "
                    "fetched, so equivalence cannot be concluded. Narrow both "
                    "queries and compare again."
                ),
            }
        out["match"] = True
        out["diff"] = ""
        return out

    only_a = {k: n - cb.get(k, 0) for k, n in ca.items() if n > cb.get(k, 0)}
    only_b = {k: n - ca.get(k, 0) for k, n in cb.items() if n > ca.get(k, 0)}

    def describe(extra: dict[tuple, int], columns: list[str]) -> list[str]:
        items = sorted(extra.items())[:_DIFF_SAMPLE]
        return [
            "(" + ", ".join(f"{c}={v}" for c, v in zip(columns, key)) + ")"
            + (f" x{n}" if n > 1 else "")
            for key, n in items
        ]

    parts = [
        f"Row counts: original {a['row_count']}, rewrite {b['row_count']}."
    ]
    if only_a:
        parts.append(
            f"{sum(only_a.values())} row(s) the original returns and the rewrite "
            f"does not, e.g. {'; '.join(describe(only_a, a['columns']))}."
        )
    if only_b:
        parts.append(
            f"{sum(only_b.values())} row(s) the rewrite returns and the original "
            f"does not, e.g. {'; '.join(describe(only_b, b['columns']))}."
        )
    # Only worth explaining when a suffix is actually shown. A row the rewrite
    # emits more often than the original -- rather than one it emits uniquely --
    # is the signature of a duplicating JOIN or UNION ALL, and it reads as a
    # puzzle without this note.
    if any(n > 1 for n in {**only_a, **only_b}.values()):
        parts.append(
            "A count suffix like x2 means that side returns the row that many "
            "extra times, which is what duplication looks like."
        )
    out["match"] = False
    out["diff"] = " ".join(parts)
    return out


# --------------------------------------------------------------------------
# Tool schemas
#
# What the model sees. It cannot read this file, so each description has to
# carry the constraint as well as the capability -- a model that does not know
# run_query refuses writes will waste a step discovering it.
# --------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": (
                "Return the test database's tables and columns, with each "
                "column's type, whether it can be NULL, and each table's row "
                "count. Call this first when NULL handling or table shape "
                "matters. Takes no arguments."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_query",
            "description": (
                "Run one read-only SELECT against the test database and return "
                "its rows. Use it to probe the data: count NULLs in a column, "
                "look for duplicates, check whether any row satisfies two "
                "conditions at once. Only a single SELECT is accepted -- "
                "multiple statements, INSERT/UPDATE/DELETE/DROP/CREATE, PRAGMA "
                "and writes of any kind are refused and not executed, and the "
                "database is open read-only. Results are capped at a few "
                "hundred rows and each query has a short time budget, so prefer "
                "aggregates like COUNT(*) over selecting everything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A single read-only SELECT statement.",
                    }
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_verdict",
            # This tool exists because of how the loop actually behaved against
            # Groq, not by design preference. Asked to put its final answer in
            # message content, openai/gpt-oss-120b instead tried to emit it as a
            # tool call named "json" -- which Groq rejects outright with
            # "attempted to call tool 'json' which was not in request.tools",
            # a 400 that killed the probing step on 2 of 3 pairs in the first
            # real eval run. A model in tool-calling mode wants to return
            # structured output through a tool, so the fix is to give it one
            # rather than to argue with it in the prompt. It also means the
            # verdict arrives against a declared schema instead of being
            # scraped out of prose.
            "description": (
                "Report your final verdict and stop. Call this exactly once, "
                "when you have finished probing. Do not put the verdict in "
                "message content; submit it here."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "verdict": {
                        "type": "string",
                        "enum": [v.value for v in Verdict],
                        "description": (
                            "equivalent_on_test_data if the results matched and "
                            "you found no input here that separates the queries "
                            "(the normal answer); not_equivalent ONLY if a "
                            "compare_results call you made returned match=false; "
                            "undetermined if you could not establish either."
                        ),
                    },
                    "evidence": {
                        "type": "string",
                        "description": (
                            "One or two plain sentences citing the numbers you "
                            "observed, and saying explicitly whether this "
                            "database exercises the risky construct."
                        ),
                    },
                },
                "required": ["verdict", "evidence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_results",
            "description": (
                "Run two SELECTs and report whether their results are identical "
                "as multisets, meaning duplicate rows are counted rather than "
                "collapsed. Returns match true/false plus a short diff of the "
                "rows that differ. Row order is ignored. This has already been "
                "run on the original and the rewrite you were given, so calling "
                "it again on that exact pair will return the same answer and "
                "waste a step; use it on variants you construct to test a "
                "specific hypothesis."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql_a": {"type": "string", "description": "First SELECT."},
                    "sql_b": {"type": "string", "description": "Second SELECT."},
                },
                "required": ["sql_a", "sql_b"],
            },
        },
    },
]


SYSTEM_PROMPT = """\
You are a SQL equivalence reviewer. You are given an original query and a \
proposed rewrite, and you decide whether the rewrite is safe.

A deterministic check has ALREADY run both queries against the test database \
and compared their results as multisets. They matched. That comparison is \
complete for this data, so you cannot and should not try to overturn it by \
re-running the same pair: on this database, these two queries return the same \
rows. That question is settled.

Your job is the question the comparison cannot answer: IS THIS TEST DATA \
STRONG ENOUGH FOR THAT MATCH TO MEAN ANYTHING? Two queries that differ only \
in how they treat NULLs return identical results on data with no NULLs. Two \
queries that differ only on duplicate rows agree perfectly on distinct data. \
So identify what kind of input WOULD separate these two queries, then use the \
tools to check whether this database actually contains it.

Work like this: look at the rewrite and name the construct that could change \
semantics -- NOT IN against a nullable column, UNION versus UNION ALL, a JOIN \
replacing EXISTS, an aggregate over a nullable column, a changed join type, a \
date boundary. Then probe for the input that would expose it: count the NULLs \
in the relevant column, count rows matching both branches of an OR, look for a \
parent row with two or more children. Prefer aggregates; you have very few \
steps.

You cannot modify the database. It is read-only.

When you are done, call the submit_verdict tool. Do not write the verdict in \
message content; submit it through that tool, exactly once.

verdict must be one of:

  "equivalent_on_test_data" -- the results matched and you found no input in \
this database that distinguishes the queries. This is the normal answer. Use \
it even if you believe the queries could differ on OTHER data; say so in the \
evidence.

  "not_equivalent" -- use this ONLY if a compare_results call you made \
actually returned match=false. A suspicion is not a mismatch.

  "undetermined" -- you could not establish either.

evidence must be one or two plain sentences a reviewer can check, citing the \
numbers you observed. Say explicitly whether this database exercises the risky \
construct. "Results matched; orders.user_id contains 1 NULL and 2 users have \
no orders, so the NOT IN case is genuinely exercised here" is useful. \
"The queries look equivalent" is not.\
"""


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

def _dispatch(name: str, args: dict, db_path: str | Path | None) -> tuple[str, bool]:
    """Execute one tool call. Returns (payload for the model, ok).

    A rejected or failed tool call is reported back to the model as a normal
    result rather than raised, for the same reason mcp_server prefers ToolError
    over a protocol error: the text reaches the model, so it can correct the
    call and keep going instead of the whole run dying on a typo.
    """
    try:
        if name == "get_schema":
            return json.dumps(get_schema(db_path)), True
        if name == "run_query":
            return json.dumps(run_query(args["sql"], db_path)), True
        if name == "compare_results":
            return json.dumps(
                compare_results(args["sql_a"], args["sql_b"], db_path)
            ), True
        return json.dumps({"error": f"No such tool: {name}."}), False
    except QueryRejected as e:
        return json.dumps({"error": str(e)}), False
    except KeyError as e:
        return json.dumps({"error": f"Missing required argument: {e}."}), False
    except Exception as e:  # noqa: BLE001 - never let a tool kill the loop
        logger.warning("Tool %s raised: %s", name, e)
        return json.dumps({"error": f"The tool failed: {e}"}), False


def _parse_verdict(text: str) -> tuple[Optional[Verdict], str]:
    """Pull {verdict, evidence} out of the model's final message."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            l for l in cleaned.split("\n") if not l.strip().startswith("```")
        )
    # The model sometimes wraps the object in a sentence; take the outermost
    # braces rather than failing the whole run over a preamble.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None, text.strip()

    if not isinstance(data, dict):
        return None, text.strip()

    evidence = str(data.get("evidence", "")).strip()
    raw = data.get("verdict")
    if isinstance(raw, str):
        try:
            return Verdict(raw.strip().lower()), evidence
        except ValueError:
            pass
    return None, evidence or text.strip()


def verify_rewrite(
    original: str,
    rewrite: str,
    db_path: str | Path | None = None,
    max_steps: Optional[int] = None,
) -> VerificationResult:
    """Check whether `rewrite` returns the same rows as `original`.

    Phase 1 is deterministic and always runs: execute both, compare as
    multisets. A mismatch is a proof of non-equivalence and returns
    immediately, with decided_without_llm=True and no network call made. A
    query that cannot be run at all yields UNDETERMINED, also without an LLM.

    Phase 2 runs only when the results matched, and only to establish whether
    the fixture actually exercises whatever could have made them differ.
    """
    max_steps = max_steps if max_steps is not None else config.VERIFY_MAX_STEPS
    calls: list[ToolCall] = []

    # ---- Phase 1: the deterministic oracle -------------------------------
    comparison = compare_results(original, rewrite, db_path)
    calls.append(
        ToolCall(
            step=0,
            tool="compare_results",
            args={"sql_a": original, "sql_b": rewrite},
            result=json.dumps(comparison)[:600],
            ok=bool(comparison.get("comparable")),
        )
    )
    logger.info(
        "verify_rewrite step 0 (deterministic): comparable=%s match=%s",
        comparison.get("comparable"), comparison.get("match"),
    )

    if not comparison.get("comparable"):
        logger.info("verify_rewrite: undetermined without the LLM")
        return VerificationResult(
            verdict=Verdict.UNDETERMINED,
            evidence=comparison.get("error", "The queries could not be compared."),
            steps_taken=1,
            decided_without_llm=True,
            tool_calls=calls,
        )

    if not comparison["match"]:
        logger.info("verify_rewrite: NOT EQUIVALENT, decided without the LLM")
        return VerificationResult(
            verdict=Verdict.NOT_EQUIVALENT,
            evidence=(
                "Proven by execution: the two queries return different results on "
                f"the test database. {comparison['diff']}"
            ),
            steps_taken=1,
            decided_without_llm=True,
            tool_calls=calls,
        )

    # ---- Phase 2: the LLM, only to probe the strength of that match ------
    matched_note = (
        f"Results matched on the test database: both return "
        f"{comparison['original']['row_count']} row(s)."
    )

    if not config.GROQ_API_KEY or not config.GROQ_API_KEY.strip():
        logger.info("verify_rewrite: no GROQ_API_KEY, reporting the match as-is")
        return VerificationResult(
            verdict=Verdict.EQUIVALENT_ON_TEST_DATA,
            evidence=(
                f"{matched_note} No LLM probing was done (GROQ_API_KEY is not "
                "set), so it is unknown whether this data exercises the cases "
                "that would distinguish the queries."
            ),
            steps_taken=1,
            decided_without_llm=True,
            probe_status="skipped",
            tool_calls=calls,
        )

    from .llm_analyzer import _get_client  # reuse the configured Groq client

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Original query:\n{original}\n\n"
                f"Proposed rewrite:\n{rewrite}\n\n"
                f"{matched_note} Determine whether this test data actually "
                "exercises whatever could make these two differ."
            ),
        },
    ]

    # max_steps is the budget for the WHOLE verification, and phase 1 already
    # spent one. So the LLM gets max_steps - 1 round trips and steps_taken can
    # never exceed max_steps -- a cap that reads the same from the outside as
    # it does here.
    steps = 1  # phase 1 counts as a step; it did real work
    llm_budget = max(1, max_steps - 1)
    verdict: Optional[Verdict] = None
    evidence = ""

    for turn in range(llm_budget):
        steps += 1
        remaining = llm_budget - turn - 1

        # A hard limit the agent cannot see is a limit it cannot respect. The
        # first live eval run lost a pair exactly this way: the model spent
        # four probes characterising the data and was cut off mid-investigation,
        # yielding "undetermined" for a rewrite that was fine. So each turn
        # states the remaining budget, and on the last one submit_verdict is
        # forced -- turning "ran out of steps" into "concluded with what it
        # had", which is a better outcome from the same budget. The cap itself
        # is unchanged.
        if remaining == 0:
            messages.append({
                "role": "user",
                "content": (
                    "This is your final step. Do not call any more probing "
                    "tools. Call submit_verdict now with whatever you have "
                    "established; if that is not enough to judge, submit "
                    "\"undetermined\" and say what you were still missing."
                ),
            })
            tool_choice: Any = {
                "type": "function",
                "function": {"name": "submit_verdict"},
            }
        else:
            messages.append({
                "role": "user",
                "content": (
                    f"You have {remaining} step(s) left before you must answer. "
                    "Call submit_verdict as soon as you can justify a verdict."
                ),
            })
            tool_choice = "auto"

        try:
            response = _get_client().chat.completions.create(
                model=config.LLM_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice=tool_choice,
                temperature=0.0,
                max_completion_tokens=config.LLM_MAX_TOKENS,
            )
        except Exception as e:  # noqa: BLE001
            # The match itself was established deterministically and still
            # stands, so the verdict is unchanged and honest. probe_status
            # carries the failure, because a verdict that reads as a success
            # would otherwise bury a broken integration -- which is exactly
            # what happened on the first live eval run.
            logger.error("verify_rewrite: Groq call failed at step %d: %s", steps, e)
            return VerificationResult(
                verdict=Verdict.EQUIVALENT_ON_TEST_DATA,
                evidence=(
                    f"{matched_note} The LLM probing step could not run ({e}), so "
                    "no distinguishing case was looked for."
                ),
                steps_taken=steps,
                probe_status="failed",
                probe_error=str(e),
                tool_calls=calls,
            )

        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []

        # No tool calls at all: the model answered in prose. Kept as a fallback
        # path because a model may ignore submit_verdict, but it is no longer
        # the expected route.
        if not tool_calls:
            verdict, evidence = _parse_verdict(message.content or "")
            logger.info(
                "verify_rewrite step %d: answered in content, verdict=%s",
                steps, verdict.value if verdict else "unparseable",
            )
            break

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        terminal = False
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            # submit_verdict ends the loop. It is recorded in the audit trail
            # like any other call, since "how did it answer" is part of the
            # trace, but it is not dispatched and gets no tool result -- there
            # is no further turn to send one to.
            if name == "submit_verdict":
                calls.append(
                    ToolCall(step=steps, tool=name, args=args,
                             result=json.dumps(args)[:600], ok=True)
                )
                raw = args.get("verdict")
                evidence = str(args.get("evidence", "")).strip()
                try:
                    verdict = Verdict(str(raw).strip().lower())
                except ValueError:
                    verdict = None
                logger.info(
                    "verify_rewrite step %d: submit_verdict -> %s",
                    steps, verdict.value if verdict else f"unrecognised ({raw!r})",
                )
                terminal = True
                break

            payload, ok = _dispatch(name, args, db_path)
            calls.append(
                ToolCall(step=steps, tool=name, args=args, result=payload[:600], ok=ok)
            )
            logger.info(
                "verify_rewrite step %d: %s(%s) -> ok=%s %s",
                steps, name, json.dumps(args)[:200], ok, payload[:200],
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": payload,
                }
            )

        if terminal:
            break
    else:
        # Loop exhausted without a final answer. This is the repetition
        # failure the step limit exists for, and it is reported as its own
        # outcome rather than being dressed up as a conclusion.
        logger.warning("verify_rewrite: step limit (%d) reached", max_steps)
        return VerificationResult(
            verdict=Verdict.UNDETERMINED,
            evidence=(
                f"{matched_note} The agent used all {max_steps} allowed steps "
                "without reaching a conclusion, so the strength of that match is "
                "unverified. Treat the rewrite as unreviewed."
            ),
            steps_taken=steps,
            probe_status="failed",
            probe_error=f"step limit of {max_steps} reached without a verdict",
            tool_calls=calls,
        )

    if verdict is None:
        return VerificationResult(
            verdict=Verdict.UNDETERMINED,
            evidence=(
                f"{matched_note} The agent's final answer could not be read as a "
                f"verdict: {evidence[:300]}"
            ),
            steps_taken=steps,
            probe_status="failed",
            probe_error="final answer was not a recognised verdict",
            tool_calls=calls,
        )

    # The model is not allowed to overturn the oracle. It was told to claim
    # not_equivalent only off a compare_results call that actually returned
    # match=false; if it says so without one, that is a hallucinated proof and
    # is downgraded rather than reported. The deterministic comparison on the
    # pair itself already matched, so the only admissible source is a variant
    # comparison the model made.
    if verdict is Verdict.NOT_EQUIVALENT and not _saw_real_mismatch(calls):
        logger.warning(
            "verify_rewrite: model claimed not_equivalent with no supporting "
            "mismatch; downgrading to equivalent_on_test_data"
        )
        return VerificationResult(
            verdict=Verdict.EQUIVALENT_ON_TEST_DATA,
            evidence=(
                f"{matched_note} The agent argued the rewrite is not equivalent "
                "but never produced a comparison that actually differed, so that "
                f"claim is not supported by evidence. Its reasoning: {evidence[:300]}"
            ),
            steps_taken=steps,
            probe_status="ok",
            tool_calls=calls,
        )

    return VerificationResult(
        verdict=verdict,
        evidence=evidence or matched_note,
        steps_taken=steps,
        probe_status="ok",
        tool_calls=calls,
    )


def _saw_real_mismatch(calls: list[ToolCall]) -> bool:
    """True if any compare_results call after phase 1 returned match=false."""
    for call in calls:
        if call.step == 0 or call.tool != "compare_results":
            continue
        try:
            data = json.loads(call.result)
        except json.JSONDecodeError:
            continue
        if data.get("comparable") and data.get("match") is False:
            return True
    return False
