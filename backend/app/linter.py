"""
SQLOps Guardian - Deterministic SQL Linter (sqlglot syntax tree)

This is Layer 1 of the architecture:
- Runs first, before RAG or LLM
- Catches obvious problems reliably every time
- No API calls, no cost, no latency
- Even if the LLM is down, this still works

Rules inspect a parsed sqlglot syntax tree rather than raw text. That matters
because three things stop being guesswork once a query is parsed:

1. Comments are dropped, so "-- DROP TABLE users" contains no Drop node.
2. String contents become opaque literals, so 'DELETE FROM users' is data.
3. Statement boundaries are real, so a WHERE in statement two cannot be
   mistaken for one belonging to the DELETE in statement one.

If sqlglot cannot parse the input we fall back to the old text-matching rules
in linter_regex.py rather than returning nothing. See the README's
"Evaluation" section for the measured difference between the two.
"""

import logging

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import ParseError

from . import linter_regex
from .models import SEVERITY_RANK, LintFinding, Severity

logger = logging.getLogger(__name__)


# ============================================
# Tree helpers
# ============================================

def _top_level_selects(statement: exp.Expression) -> list[exp.Select]:
    """
    Return the statement's outermost SELECT(s), without descending into
    subqueries or CTEs.

    Rules that care about the shape of the result set (SELECT_STAR,
    MISSING_LIMIT) must not look inside a subquery: a star or an ORDER BY
    nested in a derived table says nothing about what the query returns.
    """
    if isinstance(statement, exp.Select):
        return [statement]
    if isinstance(statement, exp.Union):
        return [
            side
            for key in ("this", "expression")
            if isinstance(side := statement.args.get(key), exp.Select)
        ]
    # INSERT INTO ... SELECT ...
    inner = statement.args.get("expression")
    if isinstance(statement, exp.Insert) and isinstance(inner, exp.Select):
        return [inner]
    return []


def _is_star(expression: exp.Expression) -> bool:
    """True for a bare `*` and for a qualified `t.*`."""
    if isinstance(expression, exp.Star):
        return True
    # `t.*` parses as a Column whose `this` is a Star.
    return isinstance(expression, exp.Column) and isinstance(expression.this, exp.Star)


# ============================================
# Rules — each takes one parsed statement
# ============================================

def check_select_star(statement: exp.Expression) -> LintFinding | None:
    """
    Detects SELECT * which:
    - Returns unnecessary columns (wastes I/O and memory)
    - Prevents index-only scans
    - Breaks if schema changes

    Only the top-level select list is examined, so COUNT(*) does not trigger
    this (its Star is a function argument, not a projected column) and neither
    does a star inside a subquery.
    """
    for select in _top_level_selects(statement):
        if any(_is_star(e) for e in select.args.get("expressions") or []):
            return LintFinding(
                rule_name="SELECT_STAR",
                severity=Severity.MEDIUM,
                description="SELECT * returns all columns. This wastes I/O, prevents index-only scans, and breaks if the schema changes.",
                suggestion="Specify only the columns you need: SELECT col1, col2, col3 FROM ...",
            )
    return None


def check_delete_without_where(statement: exp.Expression) -> LintFinding | None:
    """
    Detects DELETE without WHERE which:
    - Deletes EVERY row in the table
    - Locks the entire table
    - Usually a mistake

    Because each statement is checked on its own, a WHERE belonging to a later
    statement no longer makes a bare DELETE look safe.
    """
    if isinstance(statement, exp.Delete) and statement.args.get("where") is None:
        return LintFinding(
            rule_name="DELETE_WITHOUT_WHERE",
            severity=Severity.CRITICAL,
            description="DELETE without WHERE will delete ALL rows in the table and lock the entire table.",
            suggestion="Add a WHERE clause to target specific rows. If you intend to delete everything, use TRUNCATE instead.",
        )
    return None


def check_update_without_where(statement: exp.Expression) -> LintFinding | None:
    """
    Detects UPDATE without WHERE which:
    - Updates EVERY row in the table
    - Locks the entire table
    - Usually a mistake

    The parser normalises away alias syntax, so "UPDATE orders o SET ..." and
    "UPDATE orders AS o SET ..." are both recognised. The old regex required
    `UPDATE <word> SET` and silently skipped either form.
    """
    if isinstance(statement, exp.Update) and statement.args.get("where") is None:
        return LintFinding(
            rule_name="UPDATE_WITHOUT_WHERE",
            severity=Severity.CRITICAL,
            description="UPDATE without WHERE will modify ALL rows in the table and lock the entire table.",
            suggestion="Add a WHERE clause to target specific rows.",
        )
    return None


def check_drop_table(statement: exp.Expression) -> LintFinding | None:
    """
    Detects DROP TABLE which:
    - Permanently destroys the table and all its data
    - Irreversible without backups

    Gated on kind == "TABLE" so DROP VIEW and DROP INDEX stay quiet, matching
    the old behaviour. DROP TABLE IF EXISTS still fires.
    """
    for drop in statement.find_all(exp.Drop):
        if str(drop.args.get("kind") or "").upper() == "TABLE":
            return LintFinding(
                rule_name="DROP_TABLE",
                severity=Severity.CRITICAL,
                description="DROP TABLE permanently destroys the table and all data. This is irreversible without backups.",
                suggestion="Verify this is intentional. Consider using DROP TABLE IF EXISTS and ensure backups exist.",
            )
    return None


def check_leading_wildcard_like(statement: exp.Expression) -> LintFinding | None:
    """
    Detects LIKE '%...' which:
    - Forces a full table scan (Seq Scan)
    - B-tree indexes cannot help with leading wildcards
    - Should use full-text search (GIN index) instead

    Covers ILIKE as well as LIKE, and only fires when the pattern is a string
    literal that actually begins with '%' — a LIKE against a column or a
    parameter tells us nothing about the pattern.
    """
    for node in statement.find_all(exp.Like, exp.ILike):
        pattern = node.expression
        if (
            isinstance(pattern, exp.Literal)
            and pattern.is_string
            and str(pattern.this).startswith("%")
        ):
            return LintFinding(
                rule_name="LEADING_WILDCARD_LIKE",
                severity=Severity.MEDIUM,
                description="LIKE with a leading wildcard ('%...') forces a full table scan. B-tree indexes cannot be used.",
                suggestion="Use full-text search with a GIN index: WHERE to_tsvector('english', column) @@ to_tsquery('search_term')",
            )
    return None


def _columns_in_own_scope(node: exp.Expression) -> list[exp.Column]:
    """
    Columns belonging to `node` itself, not to a subquery nested inside it.

    A function only wraps a column if the column is one of its own arguments,
    in the same query scope. Descending into a nested SELECT would attribute
    that subquery's columns to the outer construct -- which is how EXISTS
    (a Func subclass whose body is a whole SELECT) came to look like a
    function applied to a column.
    """
    return [
        child
        for child in node.walk(
            prune=lambda n: n is not node and isinstance(n, (exp.Select, exp.Subquery))
        )
        if isinstance(child, exp.Column)
    ]


def check_function_on_column(statement: exp.Expression) -> LintFinding | None:
    """
    Detects functions wrapping columns in WHERE clauses (SARGability violation):
    - UPPER(column), LOWER(column), EXTRACT(...FROM column)
    - Prevents index usage, forces per-row function evaluation

    Fires only when the function's arguments actually contain a column, so
    WHERE created_at > DATE('2025-01-01') stays quiet: the function is applied
    to a constant once, and the column itself remains index-friendly.

    Dropping the old hardcoded list of six function names also makes this
    strictly broader — any function applied to a column is caught, not just
    the ones someone remembered to enumerate.

    Columns are collected per scope, so a subquery-bearing construct such as
    EXISTS (SELECT ... WHERE u.id = o.user_id) is not mistaken for a function
    applied to those correlated columns.
    """
    for where in statement.find_all(exp.Where):
        for func in where.find_all(exp.Func):
            # sqlglot models AND/OR as Func subclasses (via Binary), so a plain
            # "WHERE a = 1 OR a = 2" would otherwise look like a function call.
            # Connector is exactly the set of boolean operators; every real
            # function (Upper, Extract, Cast, Coalesce, Anonymous, ...) is a
            # Func that is not a Connector.
            if isinstance(func, exp.Connector):
                continue
            if _columns_in_own_scope(func):
                return LintFinding(
                    rule_name="FUNCTION_ON_COLUMN",
                    severity=Severity.HIGH,
                    description="Function wrapping a column in WHERE clause breaks index usage (SARGability). The database must evaluate the function on every row.",
                    suggestion="Rewrite to avoid the function: use range comparisons instead of EXTRACT(), functional indexes for UPPER/LOWER, or normalize data on write.",
                )
    return None


def check_missing_limit(statement: exp.Expression) -> LintFinding | None:
    """
    Detects SELECT with ORDER BY but no LIMIT which:
    - Sorts the entire result set in memory
    - Returns potentially millions of rows
    - Usually indicates missing pagination

    Reads ORDER BY off the outermost query node only. A window function's
    ORDER BY lives inside its own exp.Window node and never appears in the
    query's "order" argument, so ROW_NUMBER() OVER (ORDER BY ts) no longer
    looks like an unpaginated sort. The same is true of an ORDER BY inside a
    subquery, which does not describe what the statement returns.
    """
    if isinstance(statement, (exp.Select, exp.Union)):
        candidates: list[exp.Expression] = [statement]
    else:
        candidates = list(_top_level_selects(statement))

    for node in candidates:
        if node.args.get("order") is not None and node.args.get("limit") is None:
            return LintFinding(
                rule_name="MISSING_LIMIT",
                severity=Severity.LOW,
                description="ORDER BY without LIMIT sorts the entire result set. This can be very expensive on large tables.",
                suggestion="Add LIMIT to paginate results: ORDER BY column LIMIT 50 OFFSET 0",
            )
    return None


def check_not_in_subquery(statement: exp.Expression) -> LintFinding | None:
    """
    Detects NOT IN (SELECT ...) which:
    - Handles NULLs poorly (returns no rows if subquery contains NULL)
    - Can be slower than NOT EXISTS

    Requires both the negation and an actual subquery: "NOT IN ('x', 'y')"
    over a literal list is fine and must stay quiet, and a plain
    "IN (SELECT ...)" without NOT does not have the NULL problem.
    """
    for node in statement.find_all(exp.In):
        is_negated = isinstance(node.parent, exp.Not)
        has_subquery = node.args.get("query") is not None or any(
            isinstance(e, (exp.Select, exp.Subquery)) for e in node.args.get("expressions") or []
        )
        if is_negated and has_subquery:
            return LintFinding(
                rule_name="NOT_IN_SUBQUERY",
                severity=Severity.MEDIUM,
                description="NOT IN with a subquery handles NULLs poorly and can be slow. If the subquery returns any NULL, the entire result is empty.",
                suggestion="Use NOT EXISTS instead: WHERE NOT EXISTS (SELECT 1 FROM table WHERE condition)",
            )
    return None


def _column_keys(node: exp.Expression) -> set[str]:
    """Identify the columns referenced in a subtree, qualified where possible."""
    keys = set()
    for col in node.find_all(exp.Column):
        table = col.table
        keys.add(f"{table}.{col.name}".lower() if table else col.name.lower())
    return keys


def check_or_across_columns(statement: exp.Expression) -> LintFinding | None:
    """
    Detects OR conditions across different columns in WHERE which:
    - Prevents single index usage
    - Can be rewritten as UNION for better performance

    Compares the columns referenced on each side of every OR. Disjoint sets
    mean each branch needs a different index; overlapping sets (the common
    "status = 'new' OR status = 'pending'") are fine and stay quiet.
    """
    for where in statement.find_all(exp.Where):
        for node in where.find_all(exp.Or):
            left = _column_keys(node.this)
            right = _column_keys(node.expression)
            if left and right and left.isdisjoint(right):
                return LintFinding(
                    rule_name="OR_ACROSS_COLUMNS",
                    severity=Severity.MEDIUM,
                    description="OR across different columns prevents efficient single-index usage. PostgreSQL may fall back to a sequential scan.",
                    suggestion="Consider rewriting as UNION to let each branch use its own index, or evaluate if all conditions are necessary.",
                )
    return None


def _is_null_check(column: exp.Column, boundary: exp.Expression) -> bool:
    """
    True if `column` is used in an IS NULL / IS NOT NULL test.

    That is the deliberate anti-join idiom (LEFT JOIN ... WHERE right.id IS
    NULL, "rows with no match"), which must not be reported as the trap.
    """
    node = column.parent
    while node is not None and node is not boundary:
        if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
            return True
        node = node.parent
    return False


def check_left_join_where_trap(statement: exp.Expression) -> LintFinding | None:
    """
    Detects LEFT JOIN followed by WHERE on the right table which:
    - Effectively converts LEFT JOIN to INNER JOIN
    - Removes rows that the LEFT JOIN was meant to preserve

    The joined table is identified by its alias when it has one and by its
    table name when it does not, so all three spellings are covered:
        LEFT JOIN orders o     ON ...  WHERE o.status = 'paid'
        LEFT JOIN orders AS o  ON ...  WHERE o.status = 'paid'
        LEFT JOIN orders       ON ...  WHERE orders.status = 'paid'

    Joins are matched against the WHERE of their own SELECT, so a join in a
    subquery is judged against that subquery's filter rather than an
    unrelated one elsewhere in the statement.
    """
    for select in statement.find_all(exp.Select):
        where = select.args.get("where")
        if where is None:
            continue

        for join in select.args.get("joins") or []:
            if (join.side or "").upper() != "LEFT":
                continue

            table = join.this
            reference = (getattr(table, "alias", "") or getattr(table, "name", "") or "").lower()
            if not reference:
                continue

            for column in where.find_all(exp.Column):
                if column.table.lower() != reference:
                    continue
                if _is_null_check(column, where):
                    continue
                return LintFinding(
                    rule_name="LEFT_JOIN_WHERE_TRAP",
                    severity=Severity.HIGH,
                    description=f"WHERE clause filters on LEFT JOIN table '{reference}', which converts the LEFT JOIN to an INNER JOIN. Rows without matches are removed because the filtered column is NULL.",
                    suggestion="Either use INNER JOIN (if that's the intent) or move the condition into the ON clause to preserve unmatched rows.",
                )
    return None


# All 10 rules, each taking one parsed statement.
ALL_RULES = [
    check_select_star,
    check_delete_without_where,
    check_update_without_where,
    check_drop_table,
    check_leading_wildcard_like,
    check_function_on_column,
    check_missing_limit,
    check_not_in_subquery,
    check_or_across_columns,
    check_left_join_where_trap,
]


# ============================================
# Main linter function — runs all checks
# ============================================

# Single source of truth lives in models.SEVERITY_RANK; the pipeline orders
# severities too, and two copies could drift.
_SEVERITY_ORDER = SEVERITY_RANK


def lint_sql(sql: str) -> list[LintFinding]:
    """
    Run all deterministic checks against a SQL query.
    Returns a list of findings sorted by severity.

    Multi-statement input is parsed and checked per statement. A rule reports
    at most once per query even if it fires on several statements (first
    occurrence wins), so the shape of the output is unchanged from the
    single-finding-per-rule behaviour the API and frontend expect.
    """
    try:
        statements = [s for s in sqlglot.parse(sql) if s is not None]
    except ParseError as e:
        logger.warning(
            "sqlglot could not parse the query, falling back to regex rules: %s", e
        )
        return linter_regex.lint_sql_regex(sql)

    findings: dict[str, LintFinding] = {}

    for statement in statements:
        for rule in ALL_RULES:
            result = rule(statement)
            if result and result.rule_name not in findings:
                findings[result.rule_name] = result

    return sorted(findings.values(), key=lambda f: _SEVERITY_ORDER[f.severity])


def get_overall_severity(findings: list[LintFinding]) -> Severity:
    """Return the highest severity found."""
    if not findings:
        return Severity.LOW
    return findings[0].severity  # Already sorted, first is highest
