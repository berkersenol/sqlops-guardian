"""
SQLOps Guardian - Data Models
Using dataclasses (no external dependencies).
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
from datetime import datetime


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class LintFinding:
    """A single anti-pattern detected by the deterministic linter."""
    rule_name: str
    severity: Severity
    description: str
    suggestion: str
    line_number: Optional[int] = None


@dataclass
class AnalysisReport:
    """Complete analysis report for a SQL query."""
    query: str
    timestamp: datetime
    lint_findings: list
    overall_severity: Severity
    summary: str
    similar_cases: list = field(default_factory=list)
    llm_analysis: Optional[dict] = None
    response_time_ms: int = 0
    tokens_used: int = 0


@dataclass
class Feedback:
    """User feedback on an analysis."""
    report_id: str
    accepted: bool
    comments: Optional[str] = None
    rating: Optional[int] = None
