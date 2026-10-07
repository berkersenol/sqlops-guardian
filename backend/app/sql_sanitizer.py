"""
SQLOps Guardian - SQL sanitization for the LLM boundary.

Everything the linter and the RAG layer do happens on this machine. The LLM
call is the one point where a user's SQL leaves it, so this module is the
boundary: it decides what a third party is allowed to see, and refuses to hand
over anything it cannot vouch for.

It does two jobs at once, because both are solved by the same parse:

1. Privacy. Every literal -- string and numeric -- is replaced with a
   placeholder, so the LLM receives the shape of the query and none of the
   data in it. `WHERE email = 'alice@example.com'` becomes
   `WHERE email = :p1`. Identifiers (table and column names) are kept: they
   are the structure, and index advice is impossible without them.

2. Prompt-injection resistance. The SQL sent onward is regenerated from the
   syntax tree rather than passed through as text, so anything that is not
   SQL is gone. A comment reading "-- ignore previous instructions" has no
   node in the tree and cannot survive. Note that sqlglot's `sql()` *keeps*
   comments by default, re-emitting them as block comments, so
   `comments=False` is doing real work here.

What it refuses, and why refusing is the right default:

- A query sqlglot cannot parse. The linter falls back to regex rules in that
  case, but there is no tree to mask, and sending raw text would defeat both
  jobs above.
- A statement sqlglot parses as `exp.Command`, its container for syntax it
  does not model (VACUUM, EXPLAIN, ...). The entire remainder of the
  statement, comments included, is kept as one opaque blob. Masking it yields
  `VACUUM :p1`, which is private but structurally useless, so there is
  nothing to gain by sending it.
- Any statement type outside ALLOWED_STATEMENTS. This is an allowlist rather
  than a blocklist because literal-masking alone is not sufficient for every
  statement type: `GRANT SELECT ON users TO 'alice@example.com'` parses with
  the address as a quoted *identifier*, not a literal, so masking literals
  would not touch it. Rather than enumerate every such case, only statement
  types whose data lives in literals are allowed through.

A masked query is verified before it is returned: if any string literal from
the original still appears in the output, the result is rejected rather than
sent. That turns the guarantee from "the masking code looks right" into
something checked at runtime, on every call.
"""

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError

logger = logging.getLogger(__name__)


# Statement types whose user data lives in literals, and which therefore
# masking fully covers. Anything else is refused -- see the module docstring.
ALLOWED_STATEMENTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Merge,
)

# Only string literals at least this long are used for the leak check. A
# one- or two-character literal collides with ordinary SQL text by chance
# ("a" appears in half of all identifiers), which would reject safe queries
# for no reason. Numeric literals are masked by the same code path, so their
# coverage is not in question -- what is being verified here is that the
# masking ran at all, and strings are where identifiable data actually lives.
_MIN_VERIFIABLE_LITERAL = 3


@dataclass
class SanitizedSQL:
    """The outcome of sanitizing one query for the LLM.

    `ok` False means nothing may be sent; `reason` says why, in words meant
    for a caller to pass on to a user or a model.
    """

    ok: bool
    sql: str = ""
    reason: str = ""
    literals_masked: int = 0
    statements: int = 0
    placeholders: list[str] = field(default_factory=list)


def _safe_parse_error(error: ParseError) -> str:
    """Describe a ParseError without quoting the SQL that caused it.

    str(ParseError) embeds a snippet of the offending query, and the `highlight`
    and `start_context` fields of each entry do too. That snippet is exactly the
    data this module exists to keep in -- and `reason` is surfaced to callers
    and, through them, to a model. Only the description and position are safe
    to repeat.
    """
    parts = []
    for entry in getattr(error, "errors", []) or []:
        description = entry.get("description") or "invalid syntax"
        line, col = entry.get("line"), entry.get("col")
        parts.append(
            f"{description} at line {line}, column {col}"
            if line is not None and col is not None
            else str(description)
        )
    return "; ".join(parts) if parts else "invalid syntax"


def _string_literal_values(statements: list[exp.Expression]) -> list[str]:
    """String literal values worth checking for in the masked output."""
    values = []
    for statement in statements:
        for literal in statement.find_all(exp.Literal):
            if literal.is_string and len(str(literal.this)) >= _MIN_VERIFIABLE_LITERAL:
                values.append(str(literal.this))
    return values


def _mask_literals(statement: exp.Expression, counter: list[int], placeholders: list[str]):
    """Replace every literal in one statement with a positional placeholder.

    The counter is shared across statements so placeholder names stay unique
    across a multi-statement query. Numbering follows the tree walk rather
    than left-to-right source order, which is stable for identical input --
    that is all it needs to be.
    """

    def swap(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Literal):
            counter[0] += 1
            name = f"p{counter[0]}"
            placeholders.append(name)
            return exp.Placeholder(this=name)
        return node

    # transform copies by default, so the original tree keeps its literals and
    # can still be used for the leak check below.
    return statement.transform(swap)


def sanitize_for_llm(sql: str) -> SanitizedSQL:
    """Mask and normalize `sql` for sending to an external LLM.

    Returns a SanitizedSQL whose `ok` says whether anything may be sent at
    all. Callers must not fall back to the raw query when `ok` is False --
    that is the case this function exists to prevent.
    """
    if not sql or not sql.strip():
        return SanitizedSQL(ok=False, reason="The query was empty, so there was nothing to analyze.")

    try:
        parsed = sqlglot.parse(sql)
    except ParseError as e:
        return SanitizedSQL(
            ok=False,
            reason=(
                "The query could not be parsed, so it could not be masked "
                f"before leaving this machine ({_safe_parse_error(e)})."
            ),
        )

    # A trailing semicolon parses as its own exp.Semicolon node, which carries
    # any comment that followed it. It is punctuation, not a statement: left in
    # the list it would trip the allowlist below and refuse an ordinary query
    # for ending with "; -- note".
    statements = [
        s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)
    ]

    if not statements:
        return SanitizedSQL(ok=False, reason="The query contained no SQL statement.")

    for statement in statements:
        if isinstance(statement, exp.Command):
            return SanitizedSQL(
                ok=False,
                reason=(
                    "The query uses syntax the parser does not model, so it is "
                    "kept as opaque text that cannot be meaningfully masked."
                ),
            )
        if not isinstance(statement, ALLOWED_STATEMENTS):
            return SanitizedSQL(
                ok=False,
                reason=(
                    f"Statements of type {type(statement).__name__.upper()} are not "
                    "sent to the LLM, because their data is not confined to "
                    "literals and masking literals would not be enough."
                ),
            )

    counter = [0]
    placeholders: list[str] = []
    masked = [_mask_literals(s, counter, placeholders) for s in statements]

    # comments=False is the prompt-injection half: sqlglot re-emits comments
    # as block comments otherwise, carrying any instruction text straight
    # through into the prompt.
    rendered = "; ".join(s.sql(comments=False) for s in masked)

    leaked = [v for v in _string_literal_values(statements) if v in rendered]
    if leaked:
        # Deliberately does not log the leaked values.
        logger.error(
            "Masking verification failed: %d literal(s) survived; refusing to send.",
            len(leaked),
        )
        return SanitizedSQL(
            ok=False,
            reason=(
                "The query could not be reliably masked -- literal values "
                "survived the rewrite -- so it was not sent to the LLM."
            ),
        )

    return SanitizedSQL(
        ok=True,
        sql=rendered,
        literals_masked=counter[0],
        statements=len(statements),
        placeholders=placeholders,
    )
