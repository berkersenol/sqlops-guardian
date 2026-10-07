"""
Tests for api.py — FastAPI REST endpoints.

Converted from a hand-rolled script. As in the original, pipeline.init() is run
by a fixture and the TestClient is built without entering its context manager,
so the app's lifespan (which would re-run init) does not fire again.

The original relied on earlier requests having populated the database — the
/feedback block reused whatever /analyze had just logged. Each test here issues
the requests it depends on, against its own SQLite file.
"""

import pytest

from tests.conftest import _point_chroma, _reset_rag_globals


@pytest.fixture(scope="module")
def _initialized(tmp_path_factory):
    """Seed Chroma once for the whole module."""
    from app import config as config_mod, pipeline

    chroma_dir = tmp_path_factory.mktemp("api_chroma")
    db_file = tmp_path_factory.mktemp("api_db") / "api.db"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_mod.config, "SQLITE_DB_PATH", str(db_file))
        mp.setattr(config_mod.config, "CHROMA_PERSIST_DIR", str(chroma_dir))
        _reset_rag_globals()
        pipeline.init()

    _reset_rag_globals()
    return chroma_dir


@pytest.fixture
def client(_initialized, tmp_path, monkeypatch, no_llm):
    from fastapi.testclient import TestClient

    from app import config as config_mod
    from app.api import app
    from app.case_store import init_db

    monkeypatch.setattr(config_mod.config, "SQLITE_DB_PATH", str(tmp_path / "api.db"))
    _point_chroma(monkeypatch, _initialized)
    init_db()

    yield TestClient(app, raise_server_exceptions=False)
    _reset_rag_globals()


# --------------------------------------------------------------------------
# POST /analyze
# --------------------------------------------------------------------------

@pytest.fixture
def analyze_response(client):
    return client.post("/analyze", json={"query": "SELECT * FROM users;"})


def test_analyze_returns_200(analyze_response):
    assert analyze_response.status_code == 200


def test_analyze_has_lint_findings(analyze_response):
    assert len(analyze_response.json()["lint_findings"]) > 0


def test_analyze_detects_select_star(analyze_response):
    findings = analyze_response.json()["lint_findings"]
    assert any(f["rule_name"] == "SELECT_STAR" for f in findings)


@pytest.mark.parametrize(
    "field", ["overall_severity", "summary", "response_time_ms"]
)
def test_analyze_response_contains_field(analyze_response, field):
    assert field in analyze_response.json()


def test_analyze_has_similar_cases_list(analyze_response):
    assert isinstance(analyze_response.json().get("similar_cases"), list)


def test_analyze_clean_query_returns_200_with_no_findings(client):
    resp = client.post(
        "/analyze", json={"query": "SELECT id, name FROM users WHERE id = 1 LIMIT 10;"}
    )
    assert resp.status_code == 200
    assert len(resp.json()["lint_findings"]) == 0


def test_analyze_missing_query_returns_422(client):
    assert client.post("/analyze", json={}).status_code == 422


# --------------------------------------------------------------------------
# POST /feedback
# --------------------------------------------------------------------------

@pytest.fixture
def analysis_id(client):
    client.post("/analyze", json={"query": "SELECT * FROM users;"})
    return client.get("/recent?limit=1").json()[0]["id"]


def test_feedback_returns_200_and_ok_status(client, analysis_id):
    resp = client.post(
        "/feedback",
        json={"analysis_id": analysis_id, "accepted": True, "comments": "good suggestion"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_rejected_feedback_returns_200(client, analysis_id):
    resp = client.post(
        "/feedback",
        json={"analysis_id": analysis_id, "accepted": False, "comments": "not helpful"},
    )
    assert resp.status_code == 200


def test_feedback_missing_accepted_returns_422(client):
    assert client.post("/feedback", json={"analysis_id": 1}).status_code == 422


# --------------------------------------------------------------------------
# GET /metrics
# --------------------------------------------------------------------------

@pytest.fixture
def metrics(client):
    client.post("/analyze", json={"query": "SELECT * FROM users;"})
    client.post("/analyze", json={"query": "DELETE FROM users;"})
    return client.get("/metrics")


def test_metrics_returns_200(metrics):
    assert metrics.status_code == 200


def test_metrics_reports_total_analyses(metrics):
    body = metrics.json()
    assert "total_analyses" in body
    assert body["total_analyses"] >= 2


@pytest.mark.parametrize("field", ["acceptance_rate", "rule_counts"])
def test_metrics_contains_field(metrics, field):
    assert field in metrics.json()


# --------------------------------------------------------------------------
# GET /recent
# --------------------------------------------------------------------------

@pytest.fixture
def recent(client):
    client.post("/analyze", json={"query": "SELECT * FROM users;"})
    return client.get("/recent")


def test_recent_returns_200(recent):
    assert recent.status_code == 200


def test_recent_is_a_list_with_entries(recent):
    body = recent.json()
    assert isinstance(body, list)
    assert len(body) > 0


@pytest.mark.parametrize("field", ["id", "query"])
def test_recent_entries_contain_field(recent, field):
    assert field in recent.json()[0]


def test_recent_limit_is_respected(client):
    client.post("/analyze", json={"query": "SELECT * FROM users;"})
    client.post("/analyze", json={"query": "DELETE FROM users;"})
    assert len(client.get("/recent?limit=1").json()) == 1


def test_recent_limit_zero_returns_422(client):
    assert client.get("/recent?limit=0").status_code == 422


# --------------------------------------------------------------------------
# GET /health
# --------------------------------------------------------------------------

@pytest.fixture
def health(client):
    return client.get("/health")


def test_health_returns_200(health):
    assert health.status_code == 200


def test_health_status_is_healthy(health):
    assert health.json()["status"] == "healthy"


def test_health_reports_rag_cases(health):
    body = health.json()
    assert "rag_cases" in body
    assert body["rag_cases"] >= 0


def test_health_reports_db_connected(health):
    assert health.json()["db"] == "connected"
