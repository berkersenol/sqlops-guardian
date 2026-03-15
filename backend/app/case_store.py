"""
SQLOps Guardian - SQLite Case Store
Logs analyses, feedback, and provides metrics.
"""

import json
import sqlite3
from datetime import datetime
from typing import Optional

from .config import config
from .models import AnalysisReport, LintFinding, Severity


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the data directory and analyses table if they don't exist."""
    import os
    os.makedirs(os.path.dirname(os.path.abspath(config.SQLITE_DB_PATH)), exist_ok=True)
    conn = _get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            lint_findings TEXT NOT NULL,
            overall_severity TEXT NOT NULL,
            similar_cases TEXT NOT NULL DEFAULT '[]',
            llm_analysis TEXT,
            feedback_accepted INTEGER,
            feedback_comments TEXT,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            response_time_ms INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def log_analysis(report: AnalysisReport, response_time_ms: int = 0, tokens_used: int = 0) -> int:
    """Save an analysis report to the database. Returns the row id."""
    findings_json = json.dumps([
        {
            "rule_name": f.rule_name,
            "severity": f.severity.value,
            "description": f.description,
            "suggestion": f.suggestion,
            "line_number": f.line_number,
        }
        for f in report.lint_findings
    ])

    similar_json = json.dumps(report.similar_cases)
    llm_json = json.dumps(report.llm_analysis) if report.llm_analysis else None

    conn = _get_connection()
    cursor = conn.execute(
        """
        INSERT INTO analyses
            (query, timestamp, lint_findings, overall_severity,
             similar_cases, llm_analysis, tokens_used, response_time_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report.query,
            report.timestamp.isoformat(),
            findings_json,
            report.overall_severity.value,
            similar_json,
            llm_json,
            tokens_used,
            response_time_ms,
        ),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def log_feedback(analysis_id: int, accepted: bool, comments: Optional[str] = None):
    """Update an analysis row with user feedback."""
    conn = _get_connection()
    conn.execute(
        """
        UPDATE analyses
        SET feedback_accepted = ?, feedback_comments = ?
        WHERE id = ?
        """,
        (int(accepted), comments, analysis_id),
    )
    conn.commit()
    conn.close()


def get_recent_analyses(limit: int = 20) -> list[dict]:
    """Return the most recent analyses as a list of dicts."""
    conn = _get_connection()
    rows = conn.execute(
        "SELECT * FROM analyses ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_metrics() -> dict:
    """Return summary statistics about all logged analyses."""
    conn = _get_connection()

    total = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]

    # Most common severity
    severity_row = conn.execute(
        """
        SELECT overall_severity, COUNT(*) as cnt
        FROM analyses GROUP BY overall_severity
        ORDER BY cnt DESC LIMIT 1
        """
    ).fetchone()
    most_common_severity = severity_row["overall_severity"] if severity_row else None

    # Most common rule — parse lint_findings JSON
    all_findings = conn.execute("SELECT lint_findings FROM analyses").fetchall()
    rule_counts: dict[str, int] = {}
    for row in all_findings:
        for finding in json.loads(row["lint_findings"]):
            rule = finding["rule_name"]
            rule_counts[rule] = rule_counts.get(rule, 0) + 1
    most_common_rule = max(rule_counts, key=rule_counts.get) if rule_counts else None

    # Feedback acceptance rate
    feedback_rows = conn.execute(
        "SELECT COUNT(*) as total, SUM(feedback_accepted) as accepted "
        "FROM analyses WHERE feedback_accepted IS NOT NULL"
    ).fetchone()
    feedback_total = feedback_rows["total"]
    acceptance_rate = (
        feedback_rows["accepted"] / feedback_total if feedback_total > 0 else None
    )

    conn.close()

    return {
        "total_analyses": total,
        "most_common_severity": most_common_severity,
        "most_common_rule": most_common_rule,
        "rule_counts": rule_counts,
        "feedback_total": feedback_total,
        "acceptance_rate": acceptance_rate,
    }
