"""
SQLOps Guardian - MCP server (stdio transport).

Exposes the three analysis layers as MCP tools so any MCP host (Claude
Desktop, Cursor, Claude Code) can call them. This module is an *adapter*
only: every tool delegates to the same functions the REST API and CLI use
(app.linter, app.rag, app.pipeline) and adds nothing but argument validation
and JSON shaping.

Two constraints shape the code below.

1. stdout belongs to the protocol.
   Under stdio transport the client reads newline-delimited JSON-RPC frames
   from this process's stdout, so one stray print() corrupts the stream and
   the connection drops. mcp>=2 hardens this in the transport itself -- it
   serves the wire from a private duplicate of fd 1 and repoints fd 1 at
   stderr -- but we still configure logging to stderr explicitly, because
   that is correct under any transport and stderr is what the host captures
   into its MCP log. Nothing in this file prints.

2. SQL is data, never code.
   No tool executes, prepares, or connects to a database with the query it is
   given. It is parsed by sqlglot and embedded as text, nothing more. A
   hostile string in `query` is inert here.
"""

import logging
import sys

# Configured before importing anything from app/, so that no module-level
# logger set up during import can attach a stdout handler ahead of us.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from app import pipeline  # noqa: E402
from app.config import config  # noqa: E402
from app.linter import get_overall_severity, lint_sql as _lint_sql  # noqa: E402
from app.rag import search_similar  # noqa: E402
from app.serialization import finding_to_dict, report_to_dict  # noqa: E402

logger = logging.getLogger("sqlops_guardian.mcp")

MAX_QUERY_CHARS = 20_000

mcp = MCPServer(
    name="sqlops-guardian",
    version="0.1.0",
    instructions=(
        "SQL review tools for SQLOps Guardian. Prefer lint_sql for a quick "
        "anti-pattern check: it is local, deterministic and free. Use "
        "analyze_sql only when the user wants index suggestions or a rewrite, "
        "since it calls an external LLM. None of these tools execute the SQL "
        "they are given."
    ),
)


# --------------------------------------------------------------------------
# Shared argument validation
#
# Raising ToolError returns a normal JSON-RPC *result* with isError=true and
# this message in its content, rather than a protocol-level error. That
# distinction matters: the text reaches the model, so it can fix the call and
# retry instead of the host reporting a dead connection.
# --------------------------------------------------------------------------

_stores_ready = False


def _ensure_stores_ready() -> None:
    """Initialize SQLite and ChromaDB once, on first use rather than at startup.

    The REST API does this in its lifespan hook; a stdio server cannot. Doing
    it during startup would block the initialize handshake behind creating the
    collection and, on a cold machine, downloading the ~80MB embedding model,
    which a host may read as a failed launch. Deferring it to the first tool
    call that actually needs a store keeps startup instant and leaves lint_sql
    -- which needs neither store -- entirely unaffected.

    pipeline.init() is idempotent: it gets-or-creates the collection and only
    seeds when the collection is empty.
    """
    global _stores_ready
    if _stores_ready:
        return
    pipeline.init()
    _stores_ready = True


def _require_query(query: str) -> str:
    """Validate and normalize a SQL argument, or raise a recoverable error."""
    if not isinstance(query, str) or not query.strip():
        raise ToolError(
            "The 'query' argument was empty. Pass the SQL statement to review "
            "as a non-empty string, for example: SELECT * FROM orders;"
        )
    if len(query) > MAX_QUERY_CHARS:
        raise ToolError(
            f"The 'query' argument is {len(query)} characters, over the "
            f"{MAX_QUERY_CHARS} limit. Pass a single statement, or just the "
            "part of the script you want reviewed."
        )
    return query.strip()


def _require_top_k(top_k: int) -> int:
    """Validate top_k; a bad value is worth saying out loud rather than clamping."""
    if not isinstance(top_k, int) or isinstance(top_k, bool):
        raise ToolError("The 'top_k' argument must be an integer between 1 and 10.")
    if not 1 <= top_k <= 10:
        raise ToolError(
            f"The 'top_k' argument was {top_k}, which is outside the supported "
            "range of 1 to 10. Pass a value in that range."
        )
    return top_k


# --------------------------------------------------------------------------
# Tools
#
# The docstring of each function IS its MCP tool description, and that
# description plus the generated input schema is all the model ever sees --
# it cannot read this source. So each one states what the tool does, what it
# costs, and when to prefer a different tool.
# --------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        title="Lint SQL (local, deterministic)",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def lint_sql(query: str) -> dict:
    """Check a SQL query for known anti-patterns using a local rule-based linter.

    This is the cheap, fast, deterministic check and should be the default
    choice for "is this query OK?". It parses the SQL with sqlglot and applies
    fixed rules, so it needs no API key, makes no network call, costs nothing,
    and returns the same findings every time for the same input. Typical
    runtime is milliseconds.

    Detects issues such as SELECT *, DELETE or UPDATE without a WHERE clause,
    DROP TABLE, a missing LIMIT, leading-wildcard LIKE, a function wrapped
    around an indexed column, NOT IN against a subquery, and the LEFT JOIN
    filtered in WHERE trap.

    The query is parsed as data and is never executed against any database.

    Use analyze_sql instead only if the user wants index recommendations, a
    rewritten query, or an explanation going beyond the rule descriptions,
    and accepts a slower call to an external LLM.

    Args:
        query: The SQL statement to review. Multiple statements are allowed;
            each rule reports at most once per call.

    Returns:
        findings: One entry per detected anti-pattern, worst severity first,
            each with rule_name, severity, description and suggestion.
        finding_count: Number of findings.
        overall_severity: Worst severity present (CRITICAL, HIGH, MEDIUM or
            LOW), and LOW when the query is clean.
        clean: True when no anti-pattern was detected.
    """
    sql = _require_query(query)
    findings = _lint_sql(sql)
    logger.info("lint_sql: %d finding(s)", len(findings))
    return {
        "findings": [finding_to_dict(f) for f in findings],
        "finding_count": len(findings),
        "overall_severity": get_overall_severity(findings).value,
        "clean": not findings,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search similar past SQL cases",
        read_only_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def search_similar_cases(query: str, top_k: int = 3) -> dict:
    """Find past SQL cases similar to a query, from the local knowledge base.

    Semantic search over a local ChromaDB collection of curated anti-pattern
    cases, each recording the problems that were found and the fix that was
    applied. Useful for answering "have we seen this before, and what worked?"
    and for grounding a recommendation in a precedent rather than in a general
    opinion.

    Reads only, and runs entirely on this machine: the embedding model is
    local, so there is no API key and no external service. It is slower than
    lint_sql because the first call in a process loads that embedding model,
    but still typically well under a second afterwards.

    This returns precedents, not a verdict on the query. For an assessment of
    the query itself use lint_sql, or analyze_sql for LLM recommendations.

    The query is embedded as text and is never executed against any database.

    A vector search always returns its nearest neighbours however far away they
    are, so weak results are separated out rather than presented as matches:
    `cases` holds those at or above the similarity threshold, and
    `weak_matches` holds the rest. Treat a weak match as a hint at best, and do
    not describe it to the user as a precedent. An empty `cases` list means the
    knowledge base holds nothing close, which is a useful answer in itself.

    Args:
        query: A SQL statement, or a natural-language description of the
            problem to look for.
        top_k: How many cases to consider, from 1 to 10. Defaults to 3. This is
            the number retrieved before the similarity threshold is applied, so
            fewer may be returned as matches.

    Returns:
        cases: Genuine matches, most similar first, each with case_id, the
            original query, the fix that was applied, tables, problems, and a
            similarity score between 0 and 1 where higher is more similar.
        case_count: Number of genuine matches.
        weak_matches: Results below the threshold, same shape. Not precedents.
        min_similarity: The threshold applied.
    """
    sql = _require_query(query)
    n = _require_top_k(top_k)
    try:
        _ensure_stores_ready()
        # Lint first and feed the rule names into the search text. The linter
        # is local, free and deterministic, and the cases are indexed by the
        # problems they exhibit, so this aligns the query with the indexed
        # text: on the retrieval eval it lifts hit rate@3 from 85% to 100% and
        # raises the worst correct score from 0.329 to 0.538, which is what
        # makes a usable threshold possible at all. Non-SQL input simply
        # yields no rule names and searches as plain text.
        problems = [f.rule_name for f in _lint_sql(sql)]
        cases = search_similar(sql, problems=problems, n_results=n)
    except Exception as e:
        logger.warning("search_similar_cases failed: %s", e)
        raise ToolError(
            f"The case knowledge base could not be searched: {e}. The local "
            "ChromaDB store could not be opened or seeded, which usually means "
            "its directory is not writable. lint_sql does not depend on this "
            "store and still works, so use it to review the query instead."
        ) from e

    matches = [c for c in cases if not c["low_confidence"]]
    weak = [c for c in cases if c["low_confidence"]]

    logger.info(
        "search_similar_cases: %d match(es), %d weak", len(matches), len(weak)
    )
    return {
        "cases": matches,
        "case_count": len(matches),
        "weak_matches": weak,
        "min_similarity": config.RAG_MIN_SIMILARITY,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Full SQL analysis (calls an external LLM)",
        # Not read-only: the pipeline appends a row to the local SQLite
        # analysis log on every call. That write only ever adds history -- it
        # modifies and deletes nothing -- hence destructive_hint=False.
        read_only_hint=False,
        destructive_hint=False,
        # Same query can yield a different rewrite on each call.
        idempotent_hint=False,
        # The call leaves this machine: the query text is sent to Groq.
        open_world_hint=True,
    )
)
def analyze_sql(query: str) -> dict:
    """Run the full SQL analysis pipeline: linter, then similar cases, then an LLM.

    The most thorough tool, and the most expensive one. Beyond what lint_sql
    reports, it returns suggested CREATE INDEX statements, an optionally
    rewritten query, an estimated improvement, and a plain-English
    explanation.

    Cost and latency: this calls Groq, an external LLM service, so it is
    slower than lint_sql (typically seconds rather than milliseconds),
    consumes API tokens, and requires GROQ_API_KEY to be configured on the
    server. Prefer lint_sql when the user only wants to know whether a query
    has problems; use this tool when they want index recommendations, a
    rewrite, or reasoning about why something is slow.

    Side effect: every call appends a row -- the query, its findings and the
    timing -- to a local SQLite analysis log, which backs the project's
    metrics. Nothing in that log is modified or deleted.

    Privacy: literals are stripped before anything leaves this machine.
    Every string and number is replaced with a placeholder (:p1,
    :p2, ...), so the LLM sees the query's structure and none of its data.
    Table and column names are kept, since index advice is impossible
    without them. If a query cannot be parsed and therefore cannot be
    masked, the LLM layer is skipped rather than sent the raw text -- so a
    query that fails to parse still gets lint findings and no LLM analysis.

    The SQL is never executed against any database.

    Risk reporting: `final_risk` is floored at the worst deterministic lint
    finding, so the LLM can raise the assessed risk but never lower it. If
    the LLM rated a query less severe than the linter did, `risk_note`
    records the disagreement. Report `final_risk`, not the LLM's own
    risk_level.

    This degrades rather than failing. Each layer reports its own status, so
    if the knowledge base or the LLM is unavailable the lint findings still
    come back and `layers` says what happened. Report what did come back
    rather than treating the call as failed.

    Args:
        query: The SQL statement to analyze.

    Returns:
        lint_findings, overall_severity, summary: as from lint_sql.
        final_risk: The risk to report -- the worse of the lint severity and
            the LLM's rating, never below the lint severity.
        risk_note: Set only when the LLM's rating was overridden by that
            floor; it explains the disagreement.
        similar_cases: precedents from the knowledge base; may be empty.
        llm_analysis: suggested_indexes, rewritten_query, explanation,
            risk_level, confidence and estimated_improvement -- or null if
            the LLM was skipped or failed.
        layers: One entry per layer (linter, rag, llm, log) with its status
            -- ok, skipped or failed -- and the reason. "skipped" means the
            system chose not to run it (no API key, nothing safe to send);
            "failed" means something went wrong. These need different advice,
            so do not conflate them.
        degraded: The non-ok entries from `layers`, flattened into messages.
        response_time_ms, tokens_used: latency and token cost of this call.
    """
    sql = _require_query(query)

    try:
        _ensure_stores_ready()
        report = pipeline.analyze(sql)
    except Exception as e:
        logger.exception("analyze_sql failed")
        raise ToolError(
            f"The analysis pipeline failed: {e}. lint_sql performs the "
            "deterministic checks without the LLM or the knowledge base and "
            "is a safe fallback."
        ) from e

    result = report_to_dict(report)

    # The pipeline reports each layer's status itself, so this no longer
    # guesses from which fields came back empty. That guessing was wrong in
    # practice: a 404 from a bad model name was reported as "Groq was
    # unreachable", when the service was reachable and had rejected the
    # request. `degraded` is now a plain restatement of the layers that did
    # not succeed, using the reason the layer itself gave.
    degraded = [
        f"{layer['name']}: {layer['status']} -- {layer['reason']}"
        for layer in result["layers"]
        if layer["status"] != "ok"
    ]
    result["degraded"] = degraded

    logger.info(
        "analyze_sql: %d finding(s), final_risk=%s, layers=%s",
        len(report.lint_findings),
        report.final_risk.value,
        {layer["name"]: layer["status"] for layer in result["layers"]},
    )
    return result


def main() -> None:
    """Serve over stdio. The host spawns this process and owns its lifetime."""
    logger.info("SQLOps Guardian MCP server starting on stdio transport.")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
