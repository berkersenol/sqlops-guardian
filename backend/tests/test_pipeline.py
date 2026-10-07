"""
Tests for pipeline.py — full analysis pipeline.

Converted from a hand-rolled script. Changes worth knowing:

- The original wrote ./test_pipeline.db into the repo and let state accumulate
  across its numbered steps (step 5 asserted ">= 2 rows" because steps 2-4 had
  already run). Each test here sets up exactly the rows it asserts on.
- pipeline.init() seeds 15 Chroma cases, which dominates runtime, so it runs
  once per module and the seeded directory is shared.
- The LLM is pinned per test (`no_llm` or `mock_groq`) instead of depending on
  whether a GROQ_API_KEY happened to be present in the environment.
"""

import pytest

from app.models import Severity
from tests.conftest import _point_chroma, _reset_rag_globals


@pytest.fixture(scope="module")
def _initialized(tmp_path_factory):
    """Run pipeline.init() once; returns (chroma_dir, seeded_case_count)."""
    from app import config as config_mod, pipeline
    from app.rag import get_case_count

    chroma_dir = tmp_path_factory.mktemp("pipeline_chroma")
    db_file = tmp_path_factory.mktemp("pipeline_db") / "pipeline.db"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_mod.config, "SQLITE_DB_PATH", str(db_file))
        mp.setattr(config_mod.config, "CHROMA_PERSIST_DIR", str(chroma_dir))
        _reset_rag_globals()
        pipeline.init()
        count = get_case_count()

    _reset_rag_globals()
    return chroma_dir, count


@pytest.fixture
def env(_initialized, tmp_path, monkeypatch, no_llm):
    """Fresh SQLite per test; the seeded Chroma directory is shared."""
    from app import config as config_mod
    from app.case_store import init_db

    chroma_dir, _ = _initialized
    monkeypatch.setattr(config_mod.config, "SQLITE_DB_PATH", str(tmp_path / "pipeline.db"))
    _point_chroma(monkeypatch, chroma_dir)
    init_db()
    yield
    _reset_rag_globals()


# --------------------------------------------------------------------------
# init()
# --------------------------------------------------------------------------

def test_init_runs_without_error_and_seeds_cases(_initialized):
    _, count = _initialized
    assert count == 15


# --------------------------------------------------------------------------
# A query with real problems
# --------------------------------------------------------------------------

@pytest.fixture
def problem_report(env):
    from app import pipeline
    return pipeline.analyze(
        "SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;"
    )


def test_analyze_returns_a_report(problem_report):
    assert problem_report is not None


def test_report_has_lint_findings(problem_report):
    assert len(problem_report.lint_findings) > 0


def test_report_detects_select_star(problem_report):
    assert any(f.rule_name == "SELECT_STAR" for f in problem_report.lint_findings)


def test_report_detects_function_on_column(problem_report):
    assert any(f.rule_name == "FUNCTION_ON_COLUMN" for f in problem_report.lint_findings)


def test_overall_severity_is_high_or_above(problem_report):
    assert problem_report.overall_severity in (Severity.CRITICAL, Severity.HIGH)


def test_report_has_response_time(problem_report):
    assert problem_report.response_time_ms >= 0


def test_report_has_tokens_used_field(problem_report):
    assert hasattr(problem_report, "tokens_used")


def test_similar_cases_is_a_list(problem_report):
    assert isinstance(problem_report.similar_cases, list)


def test_report_fields_are_populated(problem_report):
    assert len(problem_report.query) > 0
    assert problem_report.timestamp is not None
    assert len(problem_report.summary) > 0


# --------------------------------------------------------------------------
# A clean query
# --------------------------------------------------------------------------

@pytest.fixture
def clean_report(env):
    from app import pipeline
    return pipeline.analyze("SELECT id, name FROM customers WHERE id = 1 LIMIT 10;")


def test_clean_query_has_no_findings(clean_report):
    assert len(clean_report.lint_findings) == 0


def test_clean_query_severity_is_low(clean_report):
    assert clean_report.overall_severity == Severity.LOW


def test_clean_query_summary_says_clean(clean_report):
    summary = clean_report.summary.lower()
    assert "clean" in summary or "no anti" in summary


# --------------------------------------------------------------------------
# Graceful degradation with no LLM
# --------------------------------------------------------------------------

@pytest.fixture
def delete_report(env):
    """`env` already pins GROQ_API_KEY to empty via the no_llm fixture."""
    from app import pipeline
    return pipeline.analyze("DELETE FROM users;")


def test_works_without_an_llm(delete_report):
    assert delete_report is not None


def test_still_detects_delete_without_where(delete_report):
    assert any(f.rule_name == "DELETE_WITHOUT_WHERE" for f in delete_report.lint_findings)


def test_llm_analysis_is_none_without_a_key(delete_report):
    assert delete_report.llm_analysis is None


# --------------------------------------------------------------------------
# UPDATE without WHERE
# --------------------------------------------------------------------------

@pytest.fixture
def update_report(env):
    from app import pipeline
    return pipeline.analyze("UPDATE customers SET status = 'inactive';")


def test_works_with_update_without_where(update_report):
    assert update_report is not None


def test_detects_update_without_where(update_report):
    assert any(f.rule_name == "UPDATE_WITHOUT_WHERE" for f in update_report.lint_findings)


def test_update_without_where_is_critical(update_report):
    assert update_report.overall_severity == Severity.CRITICAL


# --------------------------------------------------------------------------
# SQLite logging
# --------------------------------------------------------------------------

def test_analyses_are_logged_to_sqlite(env):
    from app import pipeline
    from app.case_store import get_recent_analyses

    pipeline.analyze("SELECT * FROM orders;")
    pipeline.analyze("DELETE FROM users;")

    recent = get_recent_analyses(limit=5)
    assert len(recent) >= 2
    assert any("DELETE FROM users" in r["query"] for r in recent)


# --------------------------------------------------------------------------
# LLM present (new coverage — the script could not do this without a key)
# --------------------------------------------------------------------------

def test_llm_analysis_is_attached_when_the_llm_succeeds(env, mock_groq):
    from app import pipeline

    mock_groq.set_tokens(555)
    report = pipeline.analyze("SELECT * FROM orders;")

    assert report.llm_analysis is not None
    assert report.llm_analysis["risk_level"] == "MEDIUM"
    assert report.tokens_used == 555


def test_pipeline_survives_an_llm_failure(env, mock_groq):
    from app import pipeline

    mock_groq.set_error(RuntimeError("groq is down"))
    report = pipeline.analyze("SELECT * FROM orders;")

    assert report.llm_analysis is None
    assert any(f.rule_name == "SELECT_STAR" for f in report.lint_findings)


# --------------------------------------------------------------------------
# Similarity threshold
#
# report.similar_cases goes into the LLM prompt as "similar past cases", so a
# distant neighbour there invites the model to reason from an irrelevant
# precedent. The pipeline keeps only genuine matches; weak ones are surfaced,
# labelled, by the MCP search_similar_cases tool instead.
# --------------------------------------------------------------------------

def test_low_confidence_cases_are_excluded_from_the_report(env, monkeypatch):
    from app import pipeline, rag

    def fake_search(query, problems=None, n_results=None, min_similarity=None):
        return [
            {"case_id": "strong", "similarity": 0.80, "low_confidence": False},
            {"case_id": "weak", "similarity": 0.20, "low_confidence": True},
        ]

    monkeypatch.setattr(rag, "search_similar", fake_search)
    report = pipeline.analyze("SELECT * FROM orders;")
    assert [c["case_id"] for c in report.similar_cases] == ["strong"]


def test_a_query_with_no_real_precedent_gets_no_similar_cases(env):
    """The original bug, at the pipeline layer."""
    from app import pipeline

    report = pipeline.analyze("slow scan")
    assert report.similar_cases == []


def test_a_seeded_query_still_gets_its_precedent(env):
    """The threshold must not suppress a genuine match."""
    from app import pipeline

    report = pipeline.analyze(
        "SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;"
    )
    assert any(c["case_id"] == "sarg-extract-date" for c in report.similar_cases)


def test_report_cases_are_all_above_the_threshold(env):
    from app import pipeline
    from app.config import config

    report = pipeline.analyze("SELECT * FROM orders;")
    assert all(c["similarity"] >= config.RAG_MIN_SIMILARITY for c in report.similar_cases)
