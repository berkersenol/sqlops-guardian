"""
Tests for mcp_server.py -- the MCP adapter over the analysis layers.

Design notes:

- The tool functions are tested by calling them directly. MCPServer.tool()
  registers the function and returns it unchanged, so `mcp_server.lint_sql`
  is still a plain callable; there is no need to stand up a client session or
  a subprocess to exercise the tool bodies.
- What a transport *would* add is argument coercion against the generated
  JSON schema. The `test_registered_tools` block covers that surface
  separately by inspecting what tools/list actually advertises, which is the
  only thing a model ever sees.
- The LLM is always mocked, as everywhere else in this suite: `mock_groq` for
  the success path, `no_llm` for an absent key. No test needs a key or a
  network.
- `env` mirrors the fixture in test_pipeline.py: a shared seeded Chroma
  directory (embedding 15 cases is the slowest thing in the suite) plus a
  fresh SQLite file per test, since analyze_sql writes to it.
"""

import asyncio
import json
import logging
import sys

import pytest

import mcp_server
from mcp.server.mcpserver.exceptions import ToolError
from tests.conftest import _point_chroma, _reset_rag_globals

MESSY_QUERY = "SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;"
CLEAN_QUERY = "SELECT id, name FROM customers WHERE id = 1 LIMIT 10;"


@pytest.fixture(scope="module")
def _initialized(tmp_path_factory):
    """Run pipeline.init() once for the module; returns the Chroma directory."""
    from app import config as config_mod, pipeline

    chroma_dir = tmp_path_factory.mktemp("mcp_chroma")
    db_file = tmp_path_factory.mktemp("mcp_db") / "init.db"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_mod.config, "SQLITE_DB_PATH", str(db_file))
        mp.setattr(config_mod.config, "CHROMA_PERSIST_DIR", str(chroma_dir))
        _reset_rag_globals()
        pipeline.init()

    _reset_rag_globals()
    return chroma_dir


@pytest.fixture
def env(_initialized, tmp_path, monkeypatch, no_llm):
    """Seeded Chroma plus a fresh SQLite file; LLM absent unless overridden.

    The fixture has already done what _ensure_stores_ready would do, so the
    one-shot guard is pinned closed. Without this every test would re-enter
    pipeline.init() and rebuild the Chroma client, which tripled suite
    runtime. The `init_spy` fixture reopens the guard for the few tests that
    exercise the lazy path deliberately.
    """
    from app import config as config_mod
    from app.case_store import init_db

    monkeypatch.setattr(config_mod.config, "SQLITE_DB_PATH", str(tmp_path / "mcp.db"))
    monkeypatch.setattr(mcp_server, "_stores_ready", True)
    _point_chroma(monkeypatch, _initialized)
    init_db()
    yield
    _reset_rag_globals()


# --------------------------------------------------------------------------
# lint_sql
# --------------------------------------------------------------------------

@pytest.fixture
def lint_result():
    """lint_sql touches no store, so it needs no `env`."""
    return mcp_server.lint_sql(MESSY_QUERY)


def test_lint_detects_select_star(lint_result):
    assert any(f["rule_name"] == "SELECT_STAR" for f in lint_result["findings"])


def test_lint_reports_finding_count(lint_result):
    assert lint_result["finding_count"] == len(lint_result["findings"])


def test_lint_reports_overall_severity(lint_result):
    assert lint_result["overall_severity"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def test_lint_flags_a_messy_query_as_not_clean(lint_result):
    assert lint_result["clean"] is False


def test_lint_findings_carry_a_suggestion(lint_result):
    assert all(f["suggestion"] for f in lint_result["findings"])


def test_lint_result_is_json_serializable(lint_result):
    """Severity is an enum and would not survive json.dumps unflattened."""
    assert json.loads(json.dumps(lint_result))["finding_count"] >= 1


def test_lint_marks_a_clean_query_clean():
    assert mcp_server.lint_sql(CLEAN_QUERY)["clean"] is True


def test_lint_returns_no_findings_for_a_clean_query():
    assert mcp_server.lint_sql(CLEAN_QUERY)["findings"] == []


def test_lint_is_deterministic():
    """The description promises identical output for identical input."""
    assert mcp_server.lint_sql(MESSY_QUERY) == mcp_server.lint_sql(MESSY_QUERY)


def test_lint_accepts_leading_and_trailing_whitespace():
    assert mcp_server.lint_sql("  SELECT * FROM orders;  ")["finding_count"] >= 1


# --------------------------------------------------------------------------
# lint_sql -- the query is data, never executed
# --------------------------------------------------------------------------

def test_a_drop_statement_is_reported_not_run():
    """DROP TABLE must come back as a finding; nothing executes it."""
    result = mcp_server.lint_sql("DROP TABLE users;")
    assert any(f["rule_name"] == "DROP_TABLE" for f in result["findings"])


def test_sql_in_a_string_literal_is_inert():
    """Parsing means a statement inside a literal is opaque data, not a rule hit."""
    result = mcp_server.lint_sql("SELECT id FROM audit WHERE note = 'DROP TABLE users' LIMIT 1;")
    assert not any(f["rule_name"] == "DROP_TABLE" for f in result["findings"])


# --------------------------------------------------------------------------
# Argument validation -- errors a model can act on
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "\n\t "])
def test_empty_query_raises_tool_error(bad):
    with pytest.raises(ToolError):
        mcp_server.lint_sql(bad)


def test_empty_query_message_names_the_argument():
    with pytest.raises(ToolError, match="query"):
        mcp_server.lint_sql("")


def test_empty_query_message_is_actionable():
    """The model should be told what to pass, not just that it was wrong."""
    with pytest.raises(ToolError, match="non-empty string"):
        mcp_server.lint_sql("")


def test_oversized_query_raises_tool_error():
    with pytest.raises(ToolError, match="limit"):
        mcp_server.lint_sql("SELECT 1; " * 5000)


def test_analyze_also_validates_an_empty_query():
    with pytest.raises(ToolError, match="query"):
        mcp_server.analyze_sql("")


def test_search_also_validates_an_empty_query():
    with pytest.raises(ToolError, match="query"):
        mcp_server.search_similar_cases("")


@pytest.mark.parametrize("bad_k", [0, -1, 11, 500])
def test_out_of_range_top_k_raises_tool_error(bad_k):
    with pytest.raises(ToolError, match="top_k"):
        mcp_server.search_similar_cases(MESSY_QUERY, top_k=bad_k)


def test_non_integer_top_k_raises_tool_error():
    with pytest.raises(ToolError, match="integer"):
        mcp_server.search_similar_cases(MESSY_QUERY, top_k="three")


# --------------------------------------------------------------------------
# search_similar_cases
# --------------------------------------------------------------------------

@pytest.fixture
def search_result(env):
    return mcp_server.search_similar_cases(MESSY_QUERY)


def test_search_returns_cases(search_result):
    assert len(search_result["cases"]) > 0


def test_search_reports_case_count(search_result):
    assert search_result["case_count"] == len(search_result["cases"])


def test_search_defaults_to_three_cases(search_result):
    assert search_result["case_count"] == 3


def test_search_cases_carry_a_case_id(search_result):
    assert all(c["case_id"] for c in search_result["cases"])


def test_search_cases_carry_a_fix(search_result):
    assert all("fix" in c for c in search_result["cases"])


def test_search_similarity_is_a_fraction(search_result):
    assert all(0 <= c["similarity"] <= 1 for c in search_result["cases"])


def test_search_orders_by_similarity_descending(search_result):
    scores = [c["similarity"] for c in search_result["cases"]]
    assert scores == sorted(scores, reverse=True)


def test_search_result_is_json_serializable(search_result):
    assert json.loads(json.dumps(search_result))["case_count"] == 3


def test_search_honours_top_k(env):
    assert mcp_server.search_similar_cases(MESSY_QUERY, top_k=1)["case_count"] == 1


def test_search_accepts_a_natural_language_query(env):
    result = mcp_server.search_similar_cases("query is slow because of a full table scan")
    assert result["case_count"] > 0


def test_search_failure_becomes_a_recoverable_tool_error(env, monkeypatch):
    """A broken Chroma store must point the model at lint_sql, not just fail."""
    def boom(*args, **kwargs):
        raise RuntimeError("chroma is unavailable")

    monkeypatch.setattr(mcp_server, "search_similar", boom)
    with pytest.raises(ToolError, match="lint_sql"):
        mcp_server.search_similar_cases(MESSY_QUERY)


# --------------------------------------------------------------------------
# analyze_sql -- full pipeline with the LLM mocked
# --------------------------------------------------------------------------

@pytest.fixture
def analysis(env, mock_groq):
    mock_groq.set_tokens(321)
    return mcp_server.analyze_sql(MESSY_QUERY)


def test_analyze_returns_lint_findings(analysis):
    assert len(analysis["lint_findings"]) > 0


def test_analyze_returns_a_summary(analysis):
    assert analysis["summary"]


def test_analyze_returns_similar_cases(analysis):
    assert len(analysis["similar_cases"]) > 0


def test_analyze_attaches_llm_analysis(analysis):
    assert analysis["llm_analysis"] is not None


def test_analyze_llm_analysis_has_suggested_indexes(analysis):
    assert isinstance(analysis["llm_analysis"]["suggested_indexes"], list)


def test_analyze_llm_analysis_has_an_explanation(analysis):
    assert analysis["llm_analysis"]["explanation"]


def test_analyze_reports_tokens_used(analysis):
    assert analysis["tokens_used"] == 321


def test_analyze_reports_response_time(analysis):
    assert analysis["response_time_ms"] >= 0


def test_analyze_timestamp_is_a_string(analysis):
    """A datetime would break json.dumps; report_to_dict must isoformat it."""
    assert isinstance(analysis["timestamp"], str)


def test_analyze_result_is_json_serializable(analysis):
    assert json.loads(json.dumps(analysis))["summary"] == analysis["summary"]


def test_analyze_is_not_degraded_when_every_layer_runs(analysis):
    assert analysis["degraded"] == []


# --------------------------------------------------------------------------
# analyze_sql -- graceful degradation, reported rather than hidden
# --------------------------------------------------------------------------

def test_missing_api_key_still_returns_lint_findings(env):
    """`env` pins GROQ_API_KEY to empty via no_llm."""
    result = mcp_server.analyze_sql(MESSY_QUERY)
    assert len(result["lint_findings"]) > 0


def test_missing_api_key_is_reported_in_degraded(env):
    result = mcp_server.analyze_sql(MESSY_QUERY)
    assert any("GROQ_API_KEY" in note for note in result["degraded"])


def test_missing_api_key_leaves_llm_analysis_null(env):
    assert mcp_server.analyze_sql(MESSY_QUERY)["llm_analysis"] is None


def test_groq_failure_is_reported_as_unreachable(env, mock_groq):
    """With a key present, a failure is a service problem, not a config one."""
    mock_groq.set_error(RuntimeError("503 service unavailable"))
    result = mcp_server.analyze_sql(MESSY_QUERY)
    assert any("unreachable" in note for note in result["degraded"])


def test_groq_failure_does_not_mention_the_api_key(env, mock_groq):
    mock_groq.set_error(RuntimeError("503 service unavailable"))
    result = mcp_server.analyze_sql(MESSY_QUERY)
    assert not any("GROQ_API_KEY" in note for note in result["degraded"])


def test_groq_failure_still_returns_lint_findings(env, mock_groq):
    mock_groq.set_error(RuntimeError("503 service unavailable"))
    result = mcp_server.analyze_sql(MESSY_QUERY)
    assert any(f["rule_name"] == "SELECT_STAR" for f in result["lint_findings"])


def test_degraded_notes_point_at_the_complete_lint_findings(env):
    """Each note should tell the model what it can still trust."""
    result = mcp_server.analyze_sql(MESSY_QUERY)
    assert any("lint findings are complete" in note for note in result["degraded"])


def test_a_pipeline_crash_becomes_a_recoverable_tool_error(env, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("everything is broken")

    monkeypatch.setattr(mcp_server.pipeline, "analyze", boom)
    with pytest.raises(ToolError, match="lint_sql"):
        mcp_server.analyze_sql(MESSY_QUERY)


# --------------------------------------------------------------------------
# analyze_sql -- the documented side effect
# --------------------------------------------------------------------------

def test_analyze_logs_the_analysis_to_sqlite(env, mock_groq):
    """The tool description declares this write; assert it really happens."""
    from app.case_store import get_recent_analyses

    mcp_server.analyze_sql("DELETE FROM users;")
    recent = get_recent_analyses(limit=5)
    assert any("DELETE FROM users" in r["query"] for r in recent)


def test_lint_sql_writes_nothing(env):
    """lint_sql is annotated read-only, so it must not touch the log."""
    from app.case_store import get_recent_analyses

    before = len(get_recent_analyses(limit=50))
    mcp_server.lint_sql(MESSY_QUERY)
    assert len(get_recent_analyses(limit=50)) == before


def test_search_similar_cases_writes_nothing(env):
    """search_similar_cases is annotated read-only; same requirement."""
    from app.case_store import get_recent_analyses

    before = len(get_recent_analyses(limit=50))
    mcp_server.search_similar_cases(MESSY_QUERY)
    assert len(get_recent_analyses(limit=50)) == before


# --------------------------------------------------------------------------
# Lazy store initialization
#
# A stdio server has no lifespan hook to seed in, and seeding at import time
# would block the initialize handshake behind an embedding-model download.
# --------------------------------------------------------------------------

@pytest.fixture
def init_spy(monkeypatch):
    """Count pipeline.init() calls and force the one-shot guard back open.

    The stub does not call the real init: `env` has already created and seeded
    the stores, and what these tests assert is that the adapter calls init
    exactly once on the right paths. That init itself works is covered by
    test_pipeline.py, and doing it for real here rebuilds the Chroma client
    once per test for no added coverage.
    """
    calls = []
    monkeypatch.setattr(mcp_server.pipeline, "init", lambda: calls.append(1))
    monkeypatch.setattr(mcp_server, "_stores_ready", False)
    return calls


def test_search_initializes_the_stores(env, init_spy):
    mcp_server.search_similar_cases(MESSY_QUERY)
    assert len(init_spy) == 1


def test_analyze_initializes_the_stores(env, init_spy):
    mcp_server.analyze_sql(MESSY_QUERY)
    assert len(init_spy) == 1


def test_initialization_happens_only_once(env, init_spy):
    """Paying the seeding cost on every call would make each tool slow."""
    mcp_server.search_similar_cases(MESSY_QUERY)
    mcp_server.analyze_sql(MESSY_QUERY)
    mcp_server.search_similar_cases(CLEAN_QUERY)
    assert len(init_spy) == 1


def test_lint_sql_never_initializes_the_stores(env, init_spy):
    """lint_sql is the cheap path; it must not touch Chroma or SQLite."""
    mcp_server.lint_sql(MESSY_QUERY)
    assert init_spy == []


def test_a_failing_initialization_is_a_recoverable_tool_error(env, monkeypatch):
    def boom():
        raise RuntimeError("chroma directory is read-only")

    monkeypatch.setattr(mcp_server.pipeline, "init", boom)
    monkeypatch.setattr(mcp_server, "_stores_ready", False)
    with pytest.raises(ToolError, match="lint_sql"):
        mcp_server.search_similar_cases(MESSY_QUERY)


# --------------------------------------------------------------------------
# The tools/list surface -- all the model ever sees
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def registered():
    """The advertised tool list, keyed by name."""
    tools = asyncio.run(mcp_server.mcp.list_tools())
    return {t.name: t for t in tools}


def test_exactly_three_tools_are_exposed(registered):
    assert set(registered) == {"lint_sql", "search_similar_cases", "analyze_sql"}


@pytest.mark.parametrize("name", ["lint_sql", "search_similar_cases", "analyze_sql"])
def test_every_tool_has_a_description(registered, name):
    assert registered[name].description


@pytest.mark.parametrize("name", ["lint_sql", "search_similar_cases", "analyze_sql"])
def test_every_tool_requires_a_query(registered, name):
    assert registered[name].input_schema["required"] == ["query"]


def test_top_k_is_optional_and_defaults_to_three(registered):
    prop = registered["search_similar_cases"].input_schema["properties"]["top_k"]
    assert prop["default"] == 3


def test_top_k_is_schema_typed_as_an_integer(registered):
    """Schema typing is what stops a host sending top_k="3" in the first place."""
    prop = registered["search_similar_cases"].input_schema["properties"]["top_k"]
    assert prop["type"] == "integer"


def test_lint_description_states_it_is_cheap(registered):
    assert "costs nothing" in registered["lint_sql"].description


def test_lint_description_states_it_is_deterministic(registered):
    assert "deterministic" in registered["lint_sql"].description


def test_lint_description_states_no_api_key_is_needed(registered):
    assert "no API key" in registered["lint_sql"].description


def test_analyze_description_names_the_external_llm(registered):
    assert "external LLM" in registered["analyze_sql"].description


def test_analyze_description_says_it_is_slower(registered):
    assert "slower than lint_sql" in registered["analyze_sql"].description


def test_analyze_description_declares_the_sqlite_side_effect(registered):
    assert "Side effect" in registered["analyze_sql"].description


def test_analyze_description_points_back_at_lint_sql(registered):
    """The cheap tool has to be discoverable from the expensive one."""
    assert "Prefer lint_sql" in registered["analyze_sql"].description


@pytest.mark.parametrize("name", ["lint_sql", "search_similar_cases", "analyze_sql"])
def test_every_description_says_the_sql_is_not_executed(registered, name):
    assert "never executed" in registered[name].description


# --------------------------------------------------------------------------
# Annotations -- the hints hosts use to decide how much friction to apply
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["lint_sql", "search_similar_cases"])
def test_read_only_tools_are_annotated_read_only(registered, name):
    assert registered[name].annotations.read_only_hint is True


@pytest.mark.parametrize("name", ["lint_sql", "search_similar_cases"])
def test_local_tools_are_annotated_closed_world(registered, name):
    assert registered[name].annotations.open_world_hint is False


def test_analyze_is_not_annotated_read_only(registered):
    """It writes to the SQLite analysis log."""
    assert registered["analyze_sql"].annotations.read_only_hint is False


def test_analyze_is_annotated_non_destructive(registered):
    """The write only appends history."""
    assert registered["analyze_sql"].annotations.destructive_hint is False


def test_analyze_is_annotated_open_world(registered):
    """The query text leaves the machine for Groq."""
    assert registered["analyze_sql"].annotations.open_world_hint is True


def test_annotations_serialize_to_the_camel_case_wire_names(registered):
    """The spec field is readOnlyHint; pydantic aliases the snake_case name."""
    dumped = registered["lint_sql"].annotations.model_dump(by_alias=True, exclude_none=True)
    assert dumped["readOnlyHint"] is True


# --------------------------------------------------------------------------
# stdio hygiene
# --------------------------------------------------------------------------

def _root_stream_handlers() -> list[logging.StreamHandler]:
    return [
        h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler)
    ]


def test_importing_the_server_configures_a_stream_handler():
    assert _root_stream_handlers()


def test_no_log_handler_writes_to_stdout():
    """Anything on stdout would be read as a malformed JSON-RPC frame."""
    assert all(h.stream is not sys.stdout for h in _root_stream_handlers())


def test_tools_return_dicts_not_objects():
    """A non-serializable return would fail at the transport, not here."""
    assert isinstance(mcp_server.lint_sql(CLEAN_QUERY), dict)
