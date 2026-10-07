"""
Tests for llm_analyzer.py — Groq LLM integration.

Converted from a hand-rolled script. The original file made real network calls
(one with a deliberately invalid key, plus a "live" block that only ran when
GROQ_API_KEY happened to be set). Both are now driven by the `mock_groq`
fixture, so the response-shape assertions that used to be skipped in CI run
every time and no test needs a key or a network.
"""

import json

import pytest

from app.llm_analyzer import _build_prompt, _parse_response, analyze_with_llm
from app.models import LintFinding, Severity
from tests.conftest import LLM_JSON_RESPONSE

SAMPLE_FINDINGS = [
    LintFinding("SELECT_STAR", Severity.MEDIUM, "Returns all columns", "List specific columns"),
    LintFinding("FUNCTION_ON_COLUMN", Severity.HIGH, "EXTRACT breaks SARGability", "Use range comparison"),
]

SAMPLE_CASES = [
    {
        "case_id": "sarg-extract-date",
        "problems": ["FUNCTION_ON_COLUMN", "SELECT_STAR"],
        "fix": "Replaced EXTRACT with range comparison",
        "similarity": 0.85,
    }
]

SAMPLE_QUERY = "SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;"


# --------------------------------------------------------------------------
# _build_prompt
# --------------------------------------------------------------------------

@pytest.fixture
def prompt():
    return _build_prompt(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES)


def test_prompt_includes_the_sql_query(prompt):
    assert "EXTRACT(YEAR FROM created_at)" in prompt


def test_prompt_includes_lint_findings(prompt):
    assert "SELECT_STAR" in prompt


def test_prompt_includes_similar_cases(prompt):
    assert "sarg-extract-date" in prompt


def test_prompt_asks_for_json_output(prompt):
    assert "valid JSON" in prompt


def test_prompt_handles_empty_findings_and_cases():
    empty = _build_prompt("SELECT 1;", [], [])
    assert "None detected" in empty
    assert "No similar past cases" in empty


# --------------------------------------------------------------------------
# _parse_response
# --------------------------------------------------------------------------

def test_parses_valid_json():
    raw = json.dumps({**LLM_JSON_RESPONSE, "explanation": "test"})
    assert _parse_response(raw)["explanation"] == "test"


def test_parses_markdown_wrapped_json():
    body = json.dumps({**LLM_JSON_RESPONSE, "explanation": "wrapped"})
    assert _parse_response(f"```json\n{body}\n```")["explanation"] == "wrapped"


def test_falls_back_on_invalid_json():
    assert _parse_response("This is not JSON at all, just text.")["confidence"] == "LOW"


def test_fallback_preserves_raw_text():
    result = _parse_response("This is not JSON at all, just text.")
    assert "not JSON" in result["explanation"]


# --------------------------------------------------------------------------
# analyze_with_llm — graceful degradation
# --------------------------------------------------------------------------

def test_returns_none_when_api_key_empty(no_llm):
    assert analyze_with_llm(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES) is None


def test_returns_none_on_api_error(mock_groq):
    """Previously exercised by sending a real request with an invalid key."""
    mock_groq.set_error(RuntimeError("401 invalid api key"))
    assert analyze_with_llm(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES) is None


# --------------------------------------------------------------------------
# analyze_with_llm — success path (was the key-gated "live API" block)
# --------------------------------------------------------------------------

@pytest.fixture
def llm_result(mock_groq):
    mock_groq.set_tokens(321)
    return analyze_with_llm(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES)


def test_call_returns_a_dict(llm_result):
    assert isinstance(llm_result, dict)


def test_result_has_explanation(llm_result):
    assert "explanation" in llm_result and len(llm_result["explanation"]) > 0


def test_result_has_risk_level(llm_result):
    assert llm_result.get("risk_level") in ("HIGH", "MEDIUM", "LOW")


def test_result_has_confidence(llm_result):
    assert llm_result.get("confidence") in ("HIGH", "MEDIUM", "LOW")


def test_result_has_suggested_indexes(llm_result):
    assert isinstance(llm_result.get("suggested_indexes"), list)


def test_result_tracks_tokens_used(llm_result):
    assert llm_result["tokens_used"] == 321


def test_result_tracks_response_time_ms(llm_result):
    assert "response_time_ms" in llm_result
    assert llm_result["response_time_ms"] >= 0


def test_request_uses_configured_model_and_token_limit(mock_groq):
    """New: the mock lets us assert on what we actually send to Groq."""
    from app.config import config

    analyze_with_llm(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES)
    sent = mock_groq.calls[0]
    assert sent["model"] == config.LLM_MODEL
    assert sent["max_completion_tokens"] == config.LLM_MAX_TOKENS
    assert SAMPLE_QUERY in sent["messages"][0]["content"]


def test_markdown_wrapped_llm_output_is_still_parsed(mock_groq):
    mock_groq.set_content(f"```json\n{json.dumps(LLM_JSON_RESPONSE)}\n```")
    result = analyze_with_llm(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES)
    assert result["risk_level"] == "MEDIUM"


def test_garbage_llm_output_degrades_to_low_confidence(mock_groq):
    mock_groq.set_content("total nonsense, not json")
    result = analyze_with_llm(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES)
    assert result["confidence"] == "LOW"
    assert result["tokens_used"] == 123
