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


# Ordered worst first, so the index is the rank and a lower rank is more
# severe. Both the linter (sorting findings) and the pipeline (flooring the
# reported risk at the worst finding) order severities, so the ordering lives
# here rather than being spelled out in each.
SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
)
SEVERITY_RANK: dict[Severity, int] = {s: i for i, s in enumerate(SEVERITY_ORDER)}


def worst_severity(*severities: Severity) -> Severity:
    """Return the most severe of the given severities."""
    return min(severities, key=lambda s: SEVERITY_RANK[s])


def severity_from_risk_level(risk_level: object) -> Optional[Severity]:
    """Map an LLM `risk_level` string onto a Severity, or None if unusable.

    The LLM is asked for HIGH/MEDIUM/LOW and is not trusted to comply: it may
    return something lowercase, something padded, or something else entirely.
    An unrecognised value yields None so the caller can ignore the LLM's
    opinion rather than coerce it into a number it did not mean.
    """
    if not isinstance(risk_level, str):
        return None
    try:
        return Severity(risk_level.strip().upper())
    except ValueError:
        return None


class LayerStatus(str, Enum):
    """Whether one pipeline layer ran.

    SKIPPED and FAILED are kept apart on purpose: skipped is a choice the
    system made (no API key, nothing safe to send), failed is something going
    wrong (the service errored). They need different responses from a user, so
    collapsing them into "no result" loses the actionable part.
    """

    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class LayerReport:
    """Per-layer outcome, so callers do not have to infer it from absences."""

    name: str
    status: LayerStatus
    reason: str = ""
    duration_ms: int = 0


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
    # Per-layer status, reported by the pipeline rather than inferred by
    # callers from which fields happen to be empty.
    layers: list = field(default_factory=list)
    # The risk actually reported, floored at the worst lint finding so an
    # LLM cannot talk a CRITICAL query down. Equals overall_severity unless
    # the LLM argued for something more severe.
    final_risk: Severity = Severity.LOW
    # Set when the LLM's risk_level was below the lint floor and was overridden.
    risk_note: str = ""


@dataclass
class Feedback:
    """User feedback on an analysis."""
    report_id: str
    accepted: bool
    comments: Optional[str] = None
    rating: Optional[int] = None
