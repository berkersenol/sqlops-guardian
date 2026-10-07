"""
Tests for case_store.py — SQLite logging layer.

Converted from a hand-rolled script; every original check is preserved as an
assert. The `db_path` fixture gives each test its own database file, so these
no longer depend on running in order or on cleaning up ./test_sqlops.db.
"""

import json

import pytest

from app.case_store import (
    get_metrics,
    get_recent_analyses,
    init_db,
    log_analysis,
    log_feedback,
)
from app.models import LintFinding, Severity


def test_init_db_creates_table(db_path):
    # db_path already called init_db(); a query against the table must work.
    assert get_recent_analyses(limit=1) == []


def test_init_db_is_idempotent(db_path):
    init_db()
    init_db()
    assert get_recent_analyses(limit=1) == []


def test_log_analysis_returns_first_row_id(db_path, make_report):
    assert log_analysis(make_report(), response_time_ms=42) == 1


def test_get_recent_analyses_returns_logged_row(db_path, make_report):
    log_analysis(make_report(), response_time_ms=42)
    assert len(get_recent_analyses(limit=10)) == 1


def test_stored_fields_round_trip(db_path, make_report):
    log_analysis(make_report(), response_time_ms=42)
    row = get_recent_analyses(limit=10)[0]
    findings = json.loads(row["lint_findings"])

    assert row["query"] == "SELECT * FROM users;"
    assert row["overall_severity"] == "MEDIUM"
    assert findings[0]["rule_name"] == "SELECT_STAR"
    assert row["response_time_ms"] == 42


def test_feedback_is_null_before_update(db_path, make_report):
    log_analysis(make_report())
    assert get_recent_analyses(limit=10)[0]["feedback_accepted"] is None


def test_log_feedback_updates_row(db_path, make_report):
    row_id = log_analysis(make_report())
    log_feedback(row_id, accepted=True, comments="Good catch")

    row = get_recent_analyses(limit=10)[0]
    assert row["feedback_accepted"] == 1
    assert row["feedback_comments"] == "Good catch"


def test_most_recent_analysis_comes_first(db_path, make_report):
    log_analysis(make_report())
    second = log_analysis(
        make_report(
            "DELETE FROM orders;",
            [
                LintFinding(
                    "DELETE_WITHOUT_WHERE",
                    Severity.CRITICAL,
                    "Deletes all rows",
                    "Add WHERE clause",
                )
            ],
        )
    )
    assert get_recent_analyses(limit=10)[0]["id"] == second


@pytest.fixture
def two_logged_analyses(db_path, make_report):
    """The original script's end state: two analyses, the first one accepted."""
    first = log_analysis(make_report(), response_time_ms=42)
    log_feedback(first, accepted=True, comments="Good catch")
    log_analysis(
        make_report(
            "DELETE FROM orders;",
            [
                LintFinding(
                    "DELETE_WITHOUT_WHERE",
                    Severity.CRITICAL,
                    "Deletes all rows",
                    "Add WHERE clause",
                )
            ],
        )
    )
    return get_metrics()


def test_metrics_total_analyses(two_logged_analyses):
    assert two_logged_analyses["total_analyses"] == 2


def test_metrics_most_common_severity_present(two_logged_analyses):
    assert two_logged_analyses["most_common_severity"] is not None


def test_metrics_most_common_rule_present(two_logged_analyses):
    assert two_logged_analyses["most_common_rule"] is not None


def test_metrics_acceptance_rate(two_logged_analyses):
    assert two_logged_analyses["acceptance_rate"] == 1.0


def test_metrics_rule_counts_has_entries(two_logged_analyses):
    assert len(two_logged_analyses["rule_counts"]) == 2
