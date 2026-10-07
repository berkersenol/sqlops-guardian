"""
Shared pytest fixtures.

Design notes:
- Every fixture points app.config.config at a tmp path rather than setting
  environment variables. The modules under test read config attributes at
  call time (case_store._get_connection, rag.init_collection), so patching
  the live config object is both reliable and import-order independent.
- rag keeps module-level _client/_collection singletons, so any fixture that
  repoints CHROMA_PERSIST_DIR must also clear them or the old collection
  leaks into the next test.
- The Groq client is always mocked; no test needs an API key or network.
"""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest


# --------------------------------------------------------------------------
# SQLite
# --------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point the case store at a fresh SQLite file and create the schema."""
    from app import config as config_mod
    from app.case_store import init_db

    path = tmp_path / "sqlops_test.db"
    monkeypatch.setattr(config_mod.config, "SQLITE_DB_PATH", str(path))
    init_db()
    return path


# --------------------------------------------------------------------------
# ChromaDB
# --------------------------------------------------------------------------

def _reset_rag_globals():
    from app import rag
    rag._client = None
    rag._collection = None


def _point_chroma(mp, directory):
    """Repoint Chroma at `directory` and re-initialize the collection."""
    from app import config as config_mod
    from app.rag import init_collection

    mp.setattr(config_mod.config, "CHROMA_PERSIST_DIR", str(directory))
    _reset_rag_globals()
    return init_collection()


@pytest.fixture
def empty_collection(tmp_path, monkeypatch):
    """A freshly created, empty Chroma collection in its own directory."""
    col = _point_chroma(monkeypatch, tmp_path / "chroma_empty")
    yield col
    _reset_rag_globals()


@pytest.fixture(scope="module")
def _seeded_dir(tmp_path_factory):
    """
    Seed the 15 canned cases exactly once per module — embedding 15 documents
    is the slowest thing in the suite, so it is not worth repeating per test.
    """
    directory = tmp_path_factory.mktemp("chroma_seeded")
    with pytest.MonkeyPatch.context() as mp:
        _point_chroma(mp, directory)
        from app.seed_cases import seed
        count = seed()
    _reset_rag_globals()
    return directory, count


@pytest.fixture
def seeded_collection(_seeded_dir, monkeypatch):
    """
    Activate the module-scoped seeded directory for one test.

    The re-point is function-scoped on purpose: a test using `empty_collection`
    may run in between and leave the rag singletons pointing elsewhere.
    """
    directory, count = _seeded_dir
    _point_chroma(monkeypatch, directory)
    yield count
    _reset_rag_globals()


# --------------------------------------------------------------------------
# Groq / LLM
# --------------------------------------------------------------------------

LLM_JSON_RESPONSE = {
    "suggested_indexes": ["CREATE INDEX idx_orders_created_at ON orders (created_at)"],
    "rewritten_query": "SELECT id FROM orders WHERE created_at >= '2025-01-01'",
    "explanation": "EXTRACT on a column prevents index use; compare against a range instead.",
    "risk_level": "MEDIUM",
    "confidence": "HIGH",
    "estimated_improvement": "2-5x faster",
}


def _fake_groq_response(content: str, total_tokens: int = 123):
    """Mimic the shape analyze_with_llm reads: choices[0].message.content + usage."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


@pytest.fixture
def mock_groq(monkeypatch):
    """
    Replace llm_analyzer._get_client with a stub and supply a real-looking key.

    Returns a control object:
        mock_groq.set_content(str)   -- what the model "returns"
        mock_groq.set_error(exc)     -- raise instead of returning
        mock_groq.calls              -- recorded create() kwargs
    """
    from app import config as config_mod
    from app import llm_analyzer

    state = {"content": json.dumps(LLM_JSON_RESPONSE), "error": None, "tokens": 123}
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if state["error"] is not None:
            raise state["error"]
        return _fake_groq_response(state["content"], state["tokens"])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    monkeypatch.setattr(config_mod.config, "GROQ_API_KEY", "test-key-not-real")
    monkeypatch.setattr(llm_analyzer, "_get_client", lambda: client)
    monkeypatch.setattr(llm_analyzer, "_client", None)

    control = SimpleNamespace(
        calls=calls,
        set_content=lambda c: state.__setitem__("content", c),
        set_error=lambda e: state.__setitem__("error", e),
        set_tokens=lambda t: state.__setitem__("tokens", t),
    )
    return control


@pytest.fixture
def no_llm(monkeypatch):
    """Simulate an unconfigured LLM (empty API key)."""
    from app import config as config_mod
    monkeypatch.setattr(config_mod.config, "GROQ_API_KEY", "")
    return None


# --------------------------------------------------------------------------
# Shared sample data
# --------------------------------------------------------------------------

@pytest.fixture
def make_report():
    """Factory for AnalysisReport objects (was a module-level helper in the scripts)."""
    from app.models import AnalysisReport, LintFinding, Severity

    def _make(query="SELECT * FROM users;", rules=None):
        if rules is None:
            rules = [
                LintFinding(
                    rule_name="SELECT_STAR",
                    severity=Severity.MEDIUM,
                    description="Returns all columns",
                    suggestion="List specific columns",
                )
            ]
        worst = max(rules, key=lambda f: list(Severity).index(f.severity)) if rules else None
        return AnalysisReport(
            query=query,
            timestamp=datetime.now(),
            lint_findings=rules,
            overall_severity=worst.severity if worst else Severity.LOW,
            summary=f"{len(rules)} issue(s) found",
        )

    return _make
