"""
Tests for pipeline.py — full analysis pipeline.
Run: uv run python -m pytest tests/test_pipeline.py
"""

import os
import sys

# Use a test database so we don't pollute the real one
os.environ["SQLITE_DB_PATH"] = "./test_pipeline.db"

from app import config as config_mod
config_mod.config.SQLITE_DB_PATH = "./test_pipeline.db"

from app.models import Severity

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


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

    print(f"\n{BOLD}Pipeline Tests{RESET}\n")

    # Clean up test DB from previous runs
    if os.path.exists("./test_pipeline.db"):
        os.remove("./test_pipeline.db")

    # --- Test 1: init() runs without error ---
    from app import pipeline
    try:
        pipeline.init()
        check("init() runs without error", True)
    except Exception as e:
        check(f"init() runs without error (got: {e})", False)

    # --- Test 2: Full pipeline with a problematic query ---
    report = pipeline.analyze("SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;")
    check("analyze returns AnalysisReport", report is not None)
    check("report has lint_findings", len(report.lint_findings) > 0)
    check("report detects SELECT_STAR", any(f.rule_name == "SELECT_STAR" for f in report.lint_findings))
    check("report detects FUNCTION_ON_COLUMN", any(f.rule_name == "FUNCTION_ON_COLUMN" for f in report.lint_findings))
    check("overall_severity is HIGH or above", report.overall_severity in (Severity.CRITICAL, Severity.HIGH))
    check("report has response_time_ms", report.response_time_ms >= 0)
    check("report has tokens_used field", hasattr(report, "tokens_used"))
    check("similar_cases is a list", isinstance(report.similar_cases, list))

    # --- Test 3: Clean query ---
    clean = pipeline.analyze("SELECT id, name FROM customers WHERE id = 1 LIMIT 10;")
    check("clean query has 0 findings", len(clean.lint_findings) == 0)
    check("clean query severity is LOW", clean.overall_severity == Severity.LOW)
    check("clean query summary says clean", "clean" in clean.summary.lower() or "no anti" in clean.summary.lower())

    # --- Test 4: Pipeline works with LLM unavailable (graceful degradation) ---
    saved_key = config_mod.config.GROQ_API_KEY
    config_mod.config.GROQ_API_KEY = ""
    report_no_llm = pipeline.analyze("DELETE FROM users;")
    check("works without LLM (no API key)", report_no_llm is not None)
    check("still detects DELETE_WITHOUT_WHERE", any(f.rule_name == "DELETE_WITHOUT_WHERE" for f in report_no_llm.lint_findings))
    check("llm_analysis is None without key", report_no_llm.llm_analysis is None)
    config_mod.config.GROQ_API_KEY = saved_key

    # --- Test 5: Results logged to SQLite ---
    from app.case_store import get_recent_analyses
    recent = get_recent_analyses(limit=5)
    check("analyses logged to SQLite", len(recent) >= 2)
    check("logged query matches", any("DELETE FROM users" in r["query"] for r in recent))

    # --- Test 6: Pipeline with empty ChromaDB (no similar cases crash) ---
    report_basic = pipeline.analyze("UPDATE customers SET status = 'inactive';")
    check("works with UPDATE without WHERE", report_basic is not None)
    check("detects UPDATE_WITHOUT_WHERE", any(f.rule_name == "UPDATE_WITHOUT_WHERE" for f in report_basic.lint_findings))
    check("severity is CRITICAL for UPDATE without WHERE", report_basic.overall_severity == Severity.CRITICAL)

    # --- Test 7: Report fields are all populated ---
    check("report has query text", len(report.query) > 0)
    check("report has timestamp", report.timestamp is not None)
    check("report has summary", len(report.summary) > 0)

    # --- Summary ---
    print(f"\n{'-'*40}")
    print(f"  {GREEN}Passed: {passed}{RESET}  {RED}Failed: {failed}{RESET}")
    print(f"{'-'*40}\n")

    # Cleanup
    try:
        os.remove("./test_pipeline.db")
    except OSError:
        pass

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
