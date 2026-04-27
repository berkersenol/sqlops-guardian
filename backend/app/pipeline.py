"""
SQLOps Guardian - Analysis Pipeline
Wires all layers together: Linter -> RAG -> LLM -> SQLite logging.
"""

import logging
import time

from .linter import lint_sql, get_overall_severity
from .models import AnalysisReport, Severity
from datetime import datetime

logger = logging.getLogger(__name__)


def init():
    """Initialize all backing stores. Call once at startup."""
    from .case_store import init_db
    from .rag import init_collection, get_case_count

    init_db()
    logger.info("SQLite database initialized.")

    init_collection()
    logger.info("ChromaDB collection initialized.")

    if get_case_count() == 0:
        from .seed_cases import seed
        count = seed()
        logger.info(f"ChromaDB was empty — auto-seeded {count} cases.")
    else:
        logger.info(f"ChromaDB already has {get_case_count()} cases — skipping seed.")


def analyze(sql: str) -> AnalysisReport:
    """
    Run the full analysis pipeline:
    1. Deterministic linter (always runs)
    2. RAG search for similar cases (graceful degradation)
    3. LLM analysis via Groq (graceful degradation)
    4. Log to SQLite
    """
    start = time.time()

    # --- Layer 1: Deterministic linter (always runs) ---
    findings = lint_sql(sql)
    overall = get_overall_severity(findings)

    # --- Layer 2: RAG search ---
    similar_cases = []
    try:
        from .rag import search_similar
        problem_names = [f.rule_name for f in findings]
        similar_cases = search_similar(sql, problems=problem_names)
    except Exception as e:
        logger.warning(f"RAG search failed, continuing without similar cases: {e}")

    # --- Layer 3: LLM analysis ---
    llm_result = None
    try:
        from .llm_analyzer import analyze_with_llm
        llm_result = analyze_with_llm(sql, findings, similar_cases)
    except Exception as e:
        logger.warning(f"LLM analysis failed, continuing without it: {e}")

    # --- Timing ---
    elapsed_ms = int((time.time() - start) * 1000)

    # Extract token usage from LLM result
    tokens_used = 0
    if llm_result:
        tokens_used = llm_result.get("tokens_used", 0)

    # --- Build summary ---
    if not findings:
        summary = "No anti-patterns detected. Query looks clean."
    elif overall == Severity.CRITICAL:
        summary = f"CRITICAL issues found! {len(findings)} problem(s) detected. Fix before deploying."
    elif overall == Severity.HIGH:
        summary = f"Significant issues found. {len(findings)} problem(s) detected. Review recommended."
    else:
        summary = f"{len(findings)} minor issue(s) detected. Consider optimizing."

    report = AnalysisReport(
        query=sql,
        timestamp=datetime.now(),
        lint_findings=findings,
        overall_severity=overall,
        summary=summary,
        similar_cases=similar_cases,
        llm_analysis=llm_result,
        response_time_ms=elapsed_ms,
        tokens_used=tokens_used,
    )

    # --- Layer 4: Log to SQLite ---
    try:
        from .case_store import log_analysis
        log_analysis(report, response_time_ms=elapsed_ms, tokens_used=tokens_used)
    except Exception as e:
        logger.warning(f"Failed to log analysis to SQLite: {e}")

    return report
