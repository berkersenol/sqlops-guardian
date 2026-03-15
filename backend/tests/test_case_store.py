"""
Tests for case_store.py — SQLite logging layer.
Run: uv run python -m pytest tests/test_case_store.py
"""

import os
import json
from datetime import datetime
from app.models import AnalysisReport, LintFinding, Severity

# Override DB path BEFORE importing case_store so it uses a temp file
os.environ["SQLITE_DB_PATH"] = "./test_sqlops.db"

from app.case_store import init_db, log_analysis, log_feedback, get_recent_analyses, get_metrics

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def make_report(query="SELECT * FROM users;", rules=None):
    """Helper to create a test AnalysisReport."""
    if rules is None:
        rules = [
            LintFinding(
                rule_name="SELECT_STAR",
                severity=Severity.MEDIUM,
                description="Returns all columns",
                suggestion="List specific columns",
            )
        ]
    worst = max(rules, key=lambda f: list(Severity).index(f.severity)) if rules else None
    return AnalysisReport(
        query=query,
        timestamp=datetime.now(),
        lint_findings=rules,
        overall_severity=worst.severity if worst else Severity.LOW,
        summary=f"{len(rules)} issue(s) found",
    )


def cleanup():
    """Remove test database."""
    if os.path.exists("./test_sqlops.db"):
        os.remove("./test_sqlops.db")


def run_tests():
    passed = 0
    failed = 0

    def check(name, condition):
        nonlocal passed, failed
        if condition:
            print(f"  {GREEN}PASS{RESET} {name}")
            passed += 1
        else:
            print(f"  {RED}FAIL{RESET} {name}")
            failed += 1

    cleanup()

    print(f"\n{BOLD}Case Store Tests{RESET}\n")

    # Test 1: init_db creates table without error
    init_db()
    check("init_db creates table", True)

    # Test 2: init_db is idempotent
    init_db()
    check("init_db is idempotent", True)

    # Test 3: log_analysis returns an id
    report = make_report()
    row_id = log_analysis(report, response_time_ms=42)
    check("log_analysis returns id", row_id == 1)

    # Test 4: get_recent_analyses returns the logged row
    rows = get_recent_analyses(limit=10)
    check("get_recent_analyses returns 1 row", len(rows) == 1)

    # Test 5: stored data is correct
    row = rows[0]
    findings = json.loads(row["lint_findings"])
    check("stored query matches", row["query"] == "SELECT * FROM users;")
    check("stored severity matches", row["overall_severity"] == "MEDIUM")
    check("stored findings are valid JSON", findings[0]["rule_name"] == "SELECT_STAR")
    check("response_time_ms stored", row["response_time_ms"] == 42)

    # Test 6: feedback is null before update
    check("feedback initially null", row["feedback_accepted"] is None)

    # Test 7: log_feedback updates the row
    log_feedback(row_id, accepted=True, comments="Good catch")
    rows = get_recent_analyses(limit=10)
    check("feedback_accepted updated", rows[0]["feedback_accepted"] == 1)
    check("feedback_comments updated", rows[0]["feedback_comments"] == "Good catch")

    # Test 8: log multiple analyses, check ordering
    report2 = make_report("DELETE FROM orders;", [
        LintFinding("DELETE_WITHOUT_WHERE", Severity.CRITICAL,
                     "Deletes all rows", "Add WHERE clause")
    ])
    id2 = log_analysis(report2)
    rows = get_recent_analyses(limit=10)
    check("most recent analysis first", rows[0]["id"] == id2)

    # Test 9: get_metrics
    metrics = get_metrics()
    check("total_analyses is 2", metrics["total_analyses"] == 2)
    check("most_common_severity present", metrics["most_common_severity"] is not None)
    check("most_common_rule present", metrics["most_common_rule"] is not None)
    check("acceptance_rate is 1.0", metrics["acceptance_rate"] == 1.0)
    check("rule_counts has entries", len(metrics["rule_counts"]) == 2)

    # Cleanup
    cleanup()

    print(f"\n{'-'*40}")
    print(f"  {GREEN}Passed: {passed}{RESET}  {RED}Failed: {failed}{RESET}")
    print(f"{'-'*40}\n")

    return failed == 0


if __name__ == "__main__":
    run_tests()
