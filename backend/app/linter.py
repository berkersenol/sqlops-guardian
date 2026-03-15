"""
SQLOps Guardian - Deterministic SQL Linter
Catches SQL anti-patterns without needing any AI.
Each rule is a function that returns a LintFinding or None.

This is Layer 1 of the architecture:
- Runs first, before RAG or LLM
- Catches obvious problems reliably every time
- No API calls, no cost, no latency
- Even if the LLM is down, this still works
"""

import re
from .models import LintFinding, Severity


def check_select_star(sql: str) -> LintFinding | None:
    """
    Detects SELECT * which:
    - Returns unnecessary columns (wastes I/O and memory)
    - Prevents index-only scans
    - Breaks if schema changes
    """
    pattern = r'\bSELECT\s+\*\s+FROM\b'
    if re.search(pattern, sql, re.IGNORECASE):
        return LintFinding(
            rule_name="SELECT_STAR",
            severity=Severity.MEDIUM,
            description="SELECT * returns all columns. This wastes I/O, prevents index-only scans, and breaks if the schema changes.",
            suggestion="Specify only the columns you need: SELECT col1, col2, col3 FROM ..."
        )
    return None


def check_delete_without_where(sql: str) -> LintFinding | None:
    """
    Detects DELETE without WHERE which:
    - Deletes EVERY row in the table
    - Locks the entire table
    - Usually a mistake
    """
    # Match DELETE FROM table but no WHERE follows
    pattern = r'\bDELETE\s+FROM\s+\w+'
    if re.search(pattern, sql, re.IGNORECASE):
        # Check if WHERE exists anywhere after DELETE
        delete_match = re.search(pattern, sql, re.IGNORECASE)
        remaining_sql = sql[delete_match.end():]
        if not re.search(r'\bWHERE\b', remaining_sql, re.IGNORECASE):
            return LintFinding(
                rule_name="DELETE_WITHOUT_WHERE",
                severity=Severity.CRITICAL,
                description="DELETE without WHERE will delete ALL rows in the table and lock the entire table.",
                suggestion="Add a WHERE clause to target specific rows. If you intend to delete everything, use TRUNCATE instead."
            )
    return None


def check_update_without_where(sql: str) -> LintFinding | None:
    """
    Detects UPDATE without WHERE which:
    - Updates EVERY row in the table
    - Locks the entire table
    - Usually a mistake
    """
    pattern = r'\bUPDATE\s+\w+\s+SET\b'
    if re.search(pattern, sql, re.IGNORECASE):
        update_match = re.search(pattern, sql, re.IGNORECASE)
        remaining_sql = sql[update_match.end():]
        if not re.search(r'\bWHERE\b', remaining_sql, re.IGNORECASE):
            return LintFinding(
                rule_name="UPDATE_WITHOUT_WHERE",
                severity=Severity.CRITICAL,
                description="UPDATE without WHERE will modify ALL rows in the table and lock the entire table.",
                suggestion="Add a WHERE clause to target specific rows."
            )
    return None


def check_drop_table(sql: str) -> LintFinding | None:
    """
    Detects DROP TABLE which:
    - Permanently destroys the table and all its data
    - Irreversible without backups
    """
    pattern = r'\bDROP\s+TABLE\b'
    if re.search(pattern, sql, re.IGNORECASE):
        return LintFinding(
            rule_name="DROP_TABLE",
            severity=Severity.CRITICAL,
            description="DROP TABLE permanently destroys the table and all data. This is irreversible without backups.",
            suggestion="Verify this is intentional. Consider using DROP TABLE IF EXISTS and ensure backups exist."
        )
    return None


def check_leading_wildcard_like(sql: str) -> LintFinding | None:
    """
    Detects LIKE '%...' which:
    - Forces a full table scan (Seq Scan)
    - B-tree indexes cannot help with leading wildcards
    - Should use full-text search (GIN index) instead
    """
    pattern = r"LIKE\s+'%"
    if re.search(pattern, sql, re.IGNORECASE):
        return LintFinding(
            rule_name="LEADING_WILDCARD_LIKE",
            severity=Severity.MEDIUM,
            description="LIKE with a leading wildcard ('%...') forces a full table scan. B-tree indexes cannot be used.",
            suggestion="Use full-text search with a GIN index: WHERE to_tsvector('english', column) @@ to_tsquery('search_term')"
        )
    return None


def check_function_on_column(sql: str) -> LintFinding | None:
    """
    Detects functions wrapping columns in WHERE clauses (SARGability violation):
    - UPPER(column), LOWER(column), EXTRACT(...FROM column)
    - Prevents index usage, forces per-row function evaluation
    """
    # Common functions that break SARGability
    patterns = [
        r'\bWHERE\b.*\bUPPER\s*\(',
        r'\bWHERE\b.*\bLOWER\s*\(',
        r'\bWHERE\b.*\bEXTRACT\s*\(',
        r'\bWHERE\b.*\bCAST\s*\(',
        r'\bWHERE\b.*\bCOALESCE\s*\(',
        r'\bWHERE\b.*\bDATE\s*\(',
    ]
    for pattern in patterns:
        if re.search(pattern, sql, re.IGNORECASE | re.DOTALL):
            return LintFinding(
                rule_name="FUNCTION_ON_COLUMN",
                severity=Severity.HIGH,
                description="Function wrapping a column in WHERE clause breaks index usage (SARGability). The database must evaluate the function on every row.",
                suggestion="Rewrite to avoid the function: use range comparisons instead of EXTRACT(), functional indexes for UPPER/LOWER, or normalize data on write."
            )
    return None


def check_missing_limit(sql: str) -> LintFinding | None:
    """
    Detects SELECT with ORDER BY but no LIMIT which:
    - Sorts the entire result set in memory
    - Returns potentially millions of rows
    - Usually indicates missing pagination
    """
    has_select = re.search(r'\bSELECT\b', sql, re.IGNORECASE)
    has_order_by = re.search(r'\bORDER\s+BY\b', sql, re.IGNORECASE)
    has_limit = re.search(r'\bLIMIT\b', sql, re.IGNORECASE)

    # Only flag if there's a SELECT with ORDER BY but no LIMIT
    if has_select and has_order_by and not has_limit:
        return LintFinding(
            rule_name="MISSING_LIMIT",
            severity=Severity.LOW,
            description="ORDER BY without LIMIT sorts the entire result set. This can be very expensive on large tables.",
            suggestion="Add LIMIT to paginate results: ORDER BY column LIMIT 50 OFFSET 0"
        )
    return None


def check_not_in_subquery(sql: str) -> LintFinding | None:
    """
    Detects NOT IN (SELECT ...) which:
    - Handles NULLs poorly (returns no rows if subquery contains NULL)
    - Can be slower than NOT EXISTS
    """
    pattern = r'\bNOT\s+IN\s*\(\s*SELECT\b'
    if re.search(pattern, sql, re.IGNORECASE):
        return LintFinding(
            rule_name="NOT_IN_SUBQUERY",
            severity=Severity.MEDIUM,
            description="NOT IN with a subquery handles NULLs poorly and can be slow. If the subquery returns any NULL, the entire result is empty.",
            suggestion="Use NOT EXISTS instead: WHERE NOT EXISTS (SELECT 1 FROM table WHERE condition)"
        )
    return None


def check_or_across_columns(sql: str) -> LintFinding | None:
    """
    Detects OR conditions across different columns in WHERE which:
    - Prevents single index usage
    - Can be rewritten as UNION for better performance
    """
    # Find WHERE clause
    where_match = re.search(r'\bWHERE\b(.+?)(?:GROUP BY|ORDER BY|LIMIT|HAVING|$)',
                            sql, re.IGNORECASE | re.DOTALL)
    if not where_match:
        return None

    where_clause = where_match.group(1)

    # Check if OR exists
    if not re.search(r'\bOR\b', where_clause, re.IGNORECASE):
        return None

    # Split by OR and check if different column names appear
    or_parts = re.split(r'\bOR\b', where_clause, flags=re.IGNORECASE)
    if len(or_parts) >= 2:
        # Extract column-like identifiers before operators from each part
        columns_per_part = []
        for part in or_parts:
            # Find identifiers before =, LIKE, >, <, IN, etc.
            cols = re.findall(r'(\b\w+\.\w+|\b\w+)\s*(?:LIKE|=|>|<|IN|IS)', part, re.IGNORECASE)
            columns_per_part.append(set(c.lower() for c in cols))

        # If different columns in different OR branches
        all_cols = set()
        has_different = False
        for cols in columns_per_part:
            if all_cols and cols and not cols.intersection(all_cols):
                has_different = True
            all_cols.update(cols)

        if has_different:
            return LintFinding(
                rule_name="OR_ACROSS_COLUMNS",
                severity=Severity.MEDIUM,
                description="OR across different columns prevents efficient single-index usage. PostgreSQL may fall back to a sequential scan.",
                suggestion="Consider rewriting as UNION to let each branch use its own index, or evaluate if all conditions are necessary."
            )
    return None


def check_left_join_where_trap(sql: str) -> LintFinding | None:
    """
    Detects LEFT JOIN followed by WHERE on the right table which:
    - Effectively converts LEFT JOIN to INNER JOIN
    - Removes rows that the LEFT JOIN was meant to preserve
    """
    # Find LEFT JOIN with table alias
    left_join_match = re.search(
        r'\bLEFT\s+JOIN\s+(\w+)\s+(\w+)\s+ON\b',
        sql, re.IGNORECASE
    )
    if not left_join_match:
        return None

    right_table_alias = left_join_match.group(2)

    # Check if WHERE clause references the right table alias
    where_match = re.search(r'\bWHERE\b(.+)', sql, re.IGNORECASE | re.DOTALL)
    if not where_match:
        return None

    where_clause = where_match.group(1)

    # Check if right table alias appears in WHERE (not in IS NULL/IS NOT NULL)
    alias_in_where = re.search(
        rf'\b{right_table_alias}\.\w+\s*(?:=|>|<|LIKE|BETWEEN|IN)',
        where_clause, re.IGNORECASE
    )

    if alias_in_where:
        return LintFinding(
            rule_name="LEFT_JOIN_WHERE_TRAP",
            severity=Severity.HIGH,
            description=f"WHERE clause filters on LEFT JOIN table '{right_table_alias}', which converts the LEFT JOIN to an INNER JOIN. Rows without matches are removed because the filtered column is NULL.",
            suggestion="Either use INNER JOIN (if that's the intent) or move the condition into the ON clause to preserve unmatched rows."
        )
    return None


# ============================================
# Main linter function — runs all checks
# ============================================

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


def lint_sql(sql: str) -> list[LintFinding]:
    """
    Run all deterministic checks against a SQL query.
    Returns a list of findings sorted by severity.
    """
    findings = []
    for rule in ALL_RULES:
        result = rule(sql)
        if result:
            findings.append(result)

    # Sort by severity: CRITICAL first, LOW last
    severity_order = {
        Severity.CRITICAL: 0,
        Severity.HIGH: 1,
        Severity.MEDIUM: 2,
        Severity.LOW: 3,
    }
    findings.sort(key=lambda f: severity_order[f.severity])

    return findings


def get_overall_severity(findings: list[LintFinding]) -> Severity:
    """Return the highest severity found."""
    if not findings:
        return Severity.LOW
    return findings[0].severity  # Already sorted, first is highest
