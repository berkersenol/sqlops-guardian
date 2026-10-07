"""
SQLOps Guardian - Analysis Pipeline
Wires all layers together: Linter -> RAG -> LLM -> SQLite logging.
"""

import logging
import time

from .linter import lint_sql, get_overall_severity
from .models import (
    SEVERITY_RANK,
    AnalysisReport,
    LayerReport,
    LayerStatus,
    Severity,
    severity_from_risk_level,
    worst_severity,
)
from .sql_sanitizer import sanitize_for_llm
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
    3. LLM analysis via Groq, on masked SQL (graceful degradation)
    4. Log to SQLite

    Every layer records a LayerReport on the result, so a caller can tell an
    empty field apart from a layer that was skipped or that failed, and why.
    The reported risk is floored at the worst lint finding -- see the severity
    floor below.
    """
    start = time.time()
    layers: list[LayerReport] = []

    # --- Layer 1: Deterministic linter (always runs) ---
    lint_start = time.time()
    findings = lint_sql(sql)
    overall = get_overall_severity(findings)
    layers.append(LayerReport(
        name="linter",
        status=LayerStatus.OK,
        reason=f"{len(findings)} finding(s).",
        duration_ms=int((time.time() - lint_start) * 1000),
    ))

    # --- Layer 2: RAG search ---
    similar_cases = []
    rag_start = time.time()
    try:
        from .rag import search_similar
        problem_names = [f.rule_name for f in findings]
        retrieved = search_similar(sql, problems=problem_names)
        # Keep only genuine matches. The search always returns its nearest
        # neighbours however distant, and these go into the LLM prompt as
        # "similar past cases" -- feeding it unrelated precedents invites it to
        # reason from them. Weak neighbours are surfaced, labelled as weak, by
        # the MCP search_similar_cases tool, whose job is to show retrieval.
        similar_cases = [c for c in retrieved if not c["low_confidence"]]
        dropped = len(retrieved) - len(similar_cases)
        if dropped:
            logger.info(
                "Dropped %d retrieved case(s) below the similarity threshold.", dropped
            )
        layers.append(LayerReport(
            name="rag",
            status=LayerStatus.OK,
            reason=(
                f"{len(similar_cases)} match(es)"
                + (f", {dropped} below the similarity threshold." if dropped else ".")
            ),
            duration_ms=int((time.time() - rag_start) * 1000),
        ))
    except Exception as e:
        logger.warning(f"RAG search failed, continuing without similar cases: {e}")
        layers.append(LayerReport(
            name="rag",
            status=LayerStatus.FAILED,
            reason=f"The case knowledge base could not be searched: {e}",
            duration_ms=int((time.time() - rag_start) * 1000),
        ))

    # --- Layer 3: LLM analysis ---
    #
    # This is the only layer that sends the query off this machine, so the SQL
    # is masked and regenerated from its syntax tree first. Sanitizing is a
    # precondition, not a best effort: if the query cannot be masked -- it does
    # not parse, or it is a statement type whose data is not confined to
    # literals -- the layer is skipped rather than sent raw.
    llm_result = None
    sanitized = sanitize_for_llm(sql)
    if not sanitized.ok:
        logger.info("Skipping LLM analysis: %s", sanitized.reason)
        layers.append(LayerReport(
            name="llm",
            status=LayerStatus.SKIPPED,
            reason=(
                f"Not sent to the LLM because the query could not be safely "
                f"masked first. {sanitized.reason}"
            ),
        ))
    else:
        llm_start = time.time()
        try:
            from .llm_analyzer import run_llm_analysis
            outcome = run_llm_analysis(sanitized.sql, findings, similar_cases)
            llm_result = outcome.result
            layers.append(LayerReport(
                name="llm",
                status=outcome.status,
                reason=outcome.reason or (
                    f"Analyzed with {sanitized.literals_masked} literal(s) masked."
                ),
                duration_ms=outcome.duration_ms,
            ))
        except Exception as e:
            # run_llm_analysis handles its own errors; this is the backstop for
            # anything unexpected, so one layer can never fail the whole report.
            logger.warning(f"LLM analysis failed, continuing without it: {e}")
            layers.append(LayerReport(
                name="llm",
                status=LayerStatus.FAILED,
                reason=f"The LLM layer raised an unexpected error: {e}",
                duration_ms=int((time.time() - llm_start) * 1000),
            ))

    # --- Severity floor ---
    #
    # The linter is deterministic and the LLM is not, so the LLM may argue the
    # risk up but never down: a model that calls a DELETE-without-WHERE "low
    # risk" must not be able to soften what gets reported. Disagreements are
    # recorded rather than hidden, because a model contradicting the linter is
    # worth seeing.
    final_risk = overall
    risk_note = ""
    if llm_result:
        llm_risk = severity_from_risk_level(llm_result.get("risk_level"))
        if llm_risk is not None:
            final_risk = worst_severity(overall, llm_risk)
            if SEVERITY_RANK[llm_risk] > SEVERITY_RANK[overall]:
                risk_note = (
                    f"The LLM rated this {llm_risk.value}, below the "
                    f"{overall.value} severity of the worst deterministic lint "
                    f"finding. The lint severity is authoritative and was kept."
                )
                logger.info("LLM risk %s overridden by lint floor %s.",
                            llm_risk.value, overall.value)

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
        layers=layers,
        final_risk=final_risk,
        risk_note=risk_note,
    )

    # --- Layer 4: Log to SQLite ---
    # `layers` is the same list object as report.layers, so appending here
    # still shows up on the returned report.
    log_start = time.time()
    try:
        from .case_store import log_analysis
        log_analysis(report, response_time_ms=elapsed_ms, tokens_used=tokens_used)
        layers.append(LayerReport(
            name="log",
            status=LayerStatus.OK,
            reason="Analysis recorded in the local SQLite log.",
            duration_ms=int((time.time() - log_start) * 1000),
        ))
    except Exception as e:
        logger.warning(f"Failed to log analysis to SQLite: {e}")
        layers.append(LayerReport(
            name="log",
            status=LayerStatus.FAILED,
            reason=f"The analysis could not be recorded: {e}",
            duration_ms=int((time.time() - log_start) * 1000),
        ))

    return report
