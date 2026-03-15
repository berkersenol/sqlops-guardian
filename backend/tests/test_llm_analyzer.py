"""
Tests for llm_analyzer.py — Gemini LLM integration.
Run: uv run python -m pytest tests/test_llm_analyzer.py
"""

import os
from app.models import LintFinding, Severity
from app.llm_analyzer import analyze_with_llm, _parse_response, _build_prompt

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

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


def run_tests():
    passed = 0
    failed = 0

    def check(name, condition):
        nonlocal passed, failed
        if condition:
            print(f"  {GREEN}PASS{RESET} {name}")
            passed += 1
        else:
            print(f"  {RED}FAIL{RESET} {name}")
            failed += 1

    print(f"\n{BOLD}LLM Analyzer Tests{RESET}\n")

    # --- Unit tests (no API call needed) ---

    # Test 1: _build_prompt includes query
    prompt = _build_prompt(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES)
    check("prompt includes the SQL query", "EXTRACT(YEAR FROM created_at)" in prompt)
    check("prompt includes lint findings", "SELECT_STAR" in prompt)
    check("prompt includes similar cases", "sarg-extract-date" in prompt)
    check("prompt asks for JSON output", "valid JSON" in prompt)

    # Test 2: _parse_response handles valid JSON
    valid_json = '{"suggested_indexes": [], "rewritten_query": null, "explanation": "test", "risk_level": "LOW", "confidence": "HIGH", "estimated_improvement": "none"}'
    result = _parse_response(valid_json)
    check("parses valid JSON", result["explanation"] == "test")

    # Test 3: _parse_response handles markdown-wrapped JSON
    wrapped = '```json\n{"suggested_indexes": [], "rewritten_query": null, "explanation": "wrapped", "risk_level": "LOW", "confidence": "HIGH", "estimated_improvement": "none"}\n```'
    result = _parse_response(wrapped)
    check("parses markdown-wrapped JSON", result["explanation"] == "wrapped")

    # Test 4: _parse_response falls back on garbage
    result = _parse_response("This is not JSON at all, just text.")
    check("fallback on invalid JSON", result["confidence"] == "LOW")
    check("fallback preserves raw text", "not JSON" in result["explanation"])

    # Test 5: _build_prompt handles empty findings/cases
    prompt = _build_prompt("SELECT 1;", [], [])
    check("handles empty findings", "None detected" in prompt)
    check("handles empty cases", "No similar past cases" in prompt)

    # --- Integration tests (require API key) ---

    # Test 6: returns None when API key is empty
    original_key = os.environ.get("GEMINI_API_KEY", "")
    os.environ["GEMINI_API_KEY"] = ""
    # Reload config to pick up empty key
    from app.config import Config
    from app import config as config_mod
    saved_key = config_mod.config.GEMINI_API_KEY
    config_mod.config.GEMINI_API_KEY = ""

    result = analyze_with_llm(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES)
    check("returns None when API key empty", result is None)

    # Restore key
    config_mod.config.GEMINI_API_KEY = saved_key
    os.environ["GEMINI_API_KEY"] = original_key

    # Test 7: returns None on bad API key (simulates API error)
    config_mod.config.GEMINI_API_KEY = "invalid-key-12345"
    result = analyze_with_llm(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES)
    check("returns None on API error (bad key)", result is None)

    # Restore
    config_mod.config.GEMINI_API_KEY = saved_key

    # Test 8: live API call (only if real key is available)
    if saved_key and saved_key.strip() and saved_key != "invalid-key-12345":
        print(f"\n  {BOLD}Live API test (calling Gemini)...{RESET}")
        result = analyze_with_llm(SAMPLE_QUERY, SAMPLE_FINDINGS, SAMPLE_CASES)
        check("live call returns a dict", isinstance(result, dict))
        if result:
            check("result has explanation", "explanation" in result and len(result["explanation"]) > 0)
            check("result has risk_level", result.get("risk_level") in ("HIGH", "MEDIUM", "LOW"))
            check("result has confidence", result.get("confidence") in ("HIGH", "MEDIUM", "LOW"))
            check("result has suggested_indexes", isinstance(result.get("suggested_indexes"), list))
            check("result tracks tokens_used", "tokens_used" in result)
            check("result tracks response_time_ms", "response_time_ms" in result)
            print(f"\n  {BOLD}LLM response:{RESET}")
            print(f"    Risk: {result.get('risk_level')}")
            print(f"    Confidence: {result.get('confidence')}")
            print(f"    Improvement: {result.get('estimated_improvement')}")
            print(f"    Tokens: {result.get('tokens_used')}")
            print(f"    Time: {result.get('response_time_ms')}ms")
            print(f"    Explanation: {result.get('explanation', '')[:200]}...")
    else:
        print(f"\n  {BOLD}Skipping live API test (no GEMINI_API_KEY){RESET}")

    print(f"\n{'-'*40}")
    print(f"  {GREEN}Passed: {passed}{RESET}  {RED}Failed: {failed}{RESET}")
    print(f"{'-'*40}\n")

    return failed == 0


if __name__ == "__main__":
    run_tests()
