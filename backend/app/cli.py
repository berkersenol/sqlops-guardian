"""
SQLOps Guardian - Command Line Interface
Usage:
    python -m app.cli analyze query.sql
    python -m app.cli analyze --inline "SELECT * FROM users"
    python -m app.cli test
"""

import sys
import os
import io

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from .linter import lint_sql
from .models import AnalysisReport, Severity


# Colors for terminal output
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"

SEVERITY_COLORS = {
    Severity.CRITICAL: RED,
    Severity.HIGH: RED,
    Severity.MEDIUM: YELLOW,
    Severity.LOW: BLUE,
}


def print_report(report: AnalysisReport):
    """Pretty-print an analysis report to the terminal."""

    print(f"\n{'='*60}")
    print(f"{BOLD}SQLOps Guardian - Analysis Report{RESET}")
    print(f"{'='*60}")
    print(f"Time: {report.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

    # Overall severity
    color = SEVERITY_COLORS[report.overall_severity]
    print(f"Overall Severity: {color}{BOLD}{report.overall_severity.value}{RESET}")
    print(f"Findings: {len(report.lint_findings)}")
    print(f"{'─'*60}")

    # Query preview
    query_preview = report.query.strip()
    if len(query_preview) > 200:
        query_preview = query_preview[:200] + "..."
    print(f"\n{BOLD}Query:{RESET}")
    print(f"  {query_preview}")

    # Findings
    if report.lint_findings:
        print(f"\n{BOLD}Findings:{RESET}\n")
        for i, finding in enumerate(report.lint_findings, 1):
            color = SEVERITY_COLORS[finding.severity]
            print(f"  {color}{BOLD}[{finding.severity.value}]{RESET} {finding.rule_name}")
            print(f"    Problem:    {finding.description}")
            print(f"    Suggestion: {finding.suggestion}")
            print()
    else:
        print(f"\n  {GREEN}{BOLD}No issues found!{RESET}\n")

    # Similar cases (RAG)
    if report.similar_cases:
        print(f"{BOLD}Similar Past Cases:{RESET}\n")
        for case in report.similar_cases:
            sim = case.get("similarity", "?")
            print(f"  - {case.get('case_id', 'unknown')} (similarity: {sim})")
            print(f"    Problems: {', '.join(case.get('problems', []))}")
            print(f"    Fix: {case.get('fix', 'N/A')}")
            print()

    # LLM analysis
    if report.llm_analysis:
        llm = report.llm_analysis
        print(f"{BOLD}LLM Analysis (Gemini):{RESET}\n")
        print(f"  Risk Level:  {llm.get('risk_level', 'N/A')}")
        print(f"  Confidence:  {llm.get('confidence', 'N/A')}")
        print(f"  Improvement: {llm.get('estimated_improvement', 'N/A')}")
        explanation = llm.get("explanation", "")
        if explanation:
            print(f"  Explanation: {explanation[:300]}")
        indexes = llm.get("suggested_indexes", [])
        if indexes:
            print(f"\n  {BOLD}Suggested Indexes:{RESET}")
            for idx in indexes:
                print(f"    {idx}")
        rewrite = llm.get("rewritten_query")
        if rewrite:
            print(f"\n  {BOLD}Rewritten Query:{RESET}")
            print(f"    {rewrite[:300]}")
        print()

    # Summary & timing
    print(f"{'─'*60}")
    print(f"{BOLD}Summary:{RESET} {report.summary}")
    if report.response_time_ms:
        print(f"Pipeline time: {report.response_time_ms}ms | Tokens used: {report.tokens_used}")
    print(f"{'='*60}\n")


def analyze_sql(sql: str) -> AnalysisReport:
    """Run the full analysis pipeline on a SQL query."""
    from . import pipeline
    return pipeline.analyze(sql)


def run_tests():
    """Run test queries to verify the linter works."""

    test_cases = [
        {
            "name": "Clean query",
            "sql": "SELECT id, name, email FROM customers WHERE country = 'US' LIMIT 50;",
            "expected_count": 0,
        },
        {
            "name": "SELECT *",
            "sql": "SELECT * FROM orders WHERE status = 'pending';",
            "expected_rules": ["SELECT_STAR"],
        },
        {
            "name": "DELETE without WHERE",
            "sql": "DELETE FROM orders;",
            "expected_rules": ["DELETE_WITHOUT_WHERE"],
        },
        {
            "name": "UPDATE without WHERE",
            "sql": "UPDATE customers SET country = 'US';",
            "expected_rules": ["UPDATE_WITHOUT_WHERE"],
        },
        {
            "name": "DROP TABLE",
            "sql": "DROP TABLE customers;",
            "expected_rules": ["DROP_TABLE"],
        },
        {
            "name": "Leading wildcard LIKE",
            "sql": "SELECT name FROM customers WHERE name LIKE '%enterprise%';",
            "expected_rules": ["LEADING_WILDCARD_LIKE"],
        },
        {
            "name": "SARGability - EXTRACT",
            "sql": "SELECT id FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;",
            "expected_rules": ["FUNCTION_ON_COLUMN"],
        },
        {
            "name": "SARGability - LOWER",
            "sql": "SELECT id FROM customers WHERE LOWER(email) = 'test@example.com';",
            "expected_rules": ["FUNCTION_ON_COLUMN"],
        },
        {
            "name": "ORDER BY without LIMIT",
            "sql": "SELECT id, total FROM orders ORDER BY total DESC;",
            "expected_rules": ["MISSING_LIMIT"],
        },
        {
            "name": "NOT IN subquery",
            "sql": "SELECT id FROM customers WHERE id NOT IN (SELECT customer_id FROM orders);",
            "expected_rules": ["NOT_IN_SUBQUERY"],
        },
        {
            "name": "LEFT JOIN WHERE trap",
            "sql": """SELECT c.name, o.total
                      FROM customers c
                      LEFT JOIN orders o ON c.id = o.customer_id
                      WHERE o.status = 'completed';""",
            "expected_rules": ["LEFT_JOIN_WHERE_TRAP"],
        },
        {
            "name": "Multiple issues - Scenario from practice",
            "sql": """SELECT * FROM customers c
                      LEFT JOIN orders o ON c.id = o.customer_id
                      LEFT JOIN order_items oi ON o.id = oi.order_id
                      WHERE LOWER(c.name) LIKE '%enterprise%'
                      AND o.status = 'completed'
                      ORDER BY o.created_at DESC;""",
            "expected_rules": ["SELECT_STAR", "FUNCTION_ON_COLUMN",
                             "LEADING_WILDCARD_LIKE", "MISSING_LIMIT",
                             "LEFT_JOIN_WHERE_TRAP"],
        },
    ]

    passed = 0
    failed = 0

    print(f"\n{BOLD}Running SQLOps Guardian Tests{RESET}\n")

    for test in test_cases:
        findings = lint_sql(test["sql"])
        found_rules = [f.rule_name for f in findings]

        if "expected_count" in test:
            success = len(findings) == test["expected_count"]
        else:
            success = all(r in found_rules for r in test["expected_rules"])

        if success:
            print(f"  {GREEN}✓{RESET} {test['name']}")
            passed += 1
        else:
            print(f"  {RED}✗{RESET} {test['name']}")
            expected = test.get("expected_rules", f"count={test.get('expected_count')}")
            print(f"    Expected: {expected}")
            print(f"    Found:    {found_rules}")
            failed += 1

    print(f"\n{'─'*40}")
    print(f"  {GREEN}Passed: {passed}{RESET}  {RED}Failed: {failed}{RESET}")
    print(f"{'─'*40}\n")


def main():
    if len(sys.argv) < 2:
        print(f"""
{BOLD}SQLOps Guardian{RESET} - SQL Anti-Pattern Detector

Usage:
    python -m app.cli analyze <file.sql>       Analyze a SQL file
    python -m app.cli analyze --inline "SQL"   Analyze inline SQL
    python -m app.cli test                     Run test suite
        """)
        return

    command = sys.argv[1]

    if command == "test":
        run_tests()

    elif command == "analyze":
        if len(sys.argv) < 3:
            print("Error: provide a SQL file or --inline 'SQL query'")
            return

        from . import pipeline
        pipeline.init()

        if sys.argv[2] == "--inline":
            sql = " ".join(sys.argv[3:])
        else:
            filepath = sys.argv[2]
            if not os.path.exists(filepath):
                print(f"Error: file '{filepath}' not found")
                return
            with open(filepath, "r") as f:
                sql = f.read()

        report = analyze_sql(sql)
        print_report(report)

    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
