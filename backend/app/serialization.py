"""
SQLOps Guardian - JSON serialization for the domain models.

The models are dataclasses holding enums and datetimes, neither of which is
JSON-serializable. Both transports need the same wire shape -- the REST API
returns it over HTTP, the MCP server returns it to a model -- so the mapping
lives here once instead of being hand-rolled at each edge.
"""

from .models import AnalysisReport, LayerReport, LintFinding


def layer_to_dict(layer: LayerReport) -> dict:
    """Flatten one layer's status, unwrapping the LayerStatus enum."""
    return {
        "name": layer.name,
        "status": layer.status.value,
        "reason": layer.reason,
        "duration_ms": layer.duration_ms,
    }


def finding_to_dict(finding: LintFinding) -> dict:
    """Flatten one lint finding, unwrapping the Severity enum to its value."""
    return {
        "rule_name": finding.rule_name,
        "severity": finding.severity.value,
        "description": finding.description,
        "suggestion": finding.suggestion,
        "line_number": finding.line_number,
    }


def report_to_dict(report: AnalysisReport) -> dict:
    """Flatten a full analysis report into JSON-serializable primitives."""
    return {
        "query": report.query,
        "timestamp": report.timestamp.isoformat(),
        "lint_findings": [finding_to_dict(f) for f in report.lint_findings],
        "overall_severity": report.overall_severity.value,
        "final_risk": report.final_risk.value,
        "risk_note": report.risk_note,
        "summary": report.summary,
        "similar_cases": report.similar_cases,
        "llm_analysis": report.llm_analysis,
        "layers": [layer_to_dict(layer) for layer in report.layers],
        "response_time_ms": report.response_time_ms,
        "tokens_used": report.tokens_used,
    }
