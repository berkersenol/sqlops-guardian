"""
SQLOps Guardian - FastAPI REST API
Endpoints for SQL analysis, feedback, metrics, and health checks.
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import pipeline
from app.case_store import log_feedback, get_metrics, get_recent_analyses
from app.rag import add_case, get_case_count

logger = logging.getLogger(__name__)


# --- Request / Response schemas ---

class AnalyzeRequest(BaseModel):
    query: str


class FeedbackRequest(BaseModel):
    analysis_id: int
    accepted: bool
    comments: Optional[str] = None


class FeedbackResponse(BaseModel):
    status: str


class HealthResponse(BaseModel):
    status: str
    rag_cases: int
    db: str


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline.init()
    logger.info("Pipeline initialized.")
    yield


# --- App ---

app = FastAPI(
    title="SQLOps Guardian",
    description="SQL analysis tool that catches anti-patterns and suggests optimizations.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Endpoints ---

@app.post("/analyze")
def analyze_sql(req: AnalyzeRequest):
    """Run the full analysis pipeline on a SQL query."""
    try:
        report = pipeline.analyze(req.query)
        return {
            "query": report.query,
            "timestamp": report.timestamp.isoformat(),
            "lint_findings": [
                {
                    "rule_name": f.rule_name,
                    "severity": f.severity.value,
                    "description": f.description,
                    "suggestion": f.suggestion,
                    "line_number": f.line_number,
                }
                for f in report.lint_findings
            ],
            "overall_severity": report.overall_severity.value,
            "summary": report.summary,
            "similar_cases": report.similar_cases,
            "llm_analysis": report.llm_analysis,
            "response_time_ms": report.response_time_ms,
            "tokens_used": report.tokens_used,
        }
    except Exception as e:
        logger.exception("Analysis failed")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")


@app.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest):
    """Submit feedback for a previous analysis."""
    try:
        log_feedback(req.analysis_id, req.accepted, req.comments)

        if req.accepted:
            # Fetch the analysis to store in RAG
            recent = get_recent_analyses(limit=100)
            analysis = next((a for a in recent if a["id"] == req.analysis_id), None)
            if analysis:
                import json
                findings = json.loads(analysis["lint_findings"])
                problems = [f["rule_name"] for f in findings]
                add_case(
                    case_id=f"feedback-{req.analysis_id}",
                    query=analysis["query"],
                    problems=problems,
                    fix=req.comments or "Accepted by user",
                    tables=[],
                )

        return FeedbackResponse(status="ok")
    except Exception as e:
        logger.exception("Feedback submission failed")
        raise HTTPException(status_code=500, detail=f"Feedback failed: {e}")


@app.get("/metrics")
def metrics():
    """Return summary statistics about all logged analyses."""
    try:
        return get_metrics()
    except Exception as e:
        logger.exception("Metrics retrieval failed")
        raise HTTPException(status_code=500, detail=f"Metrics failed: {e}")


@app.get("/recent")
def recent_analyses(limit: int = Query(default=20, ge=1, le=100)):
    """Return the most recent analyses."""
    try:
        return get_recent_analyses(limit=limit)
    except Exception as e:
        logger.exception("Recent analyses retrieval failed")
        raise HTTPException(status_code=500, detail=f"Recent analyses failed: {e}")


@app.get("/health", response_model=HealthResponse)
def health_check():
    """Check that ChromaDB and SQLite are reachable."""
    try:
        rag_cases = get_case_count()
    except Exception:
        rag_cases = -1

    try:
        get_recent_analyses(limit=1)
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    status = "healthy" if db_status == "connected" and rag_cases >= 0 else "degraded"
    return HealthResponse(status=status, rag_cases=rag_cases, db=db_status)
