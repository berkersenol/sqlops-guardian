"""
SQLOps Guardian - LLM Analyzer
Sends SQL queries + lint findings + RAG cases to Gemini for deeper analysis.
"""

import json
import logging
import time
from typing import Optional

from google import genai

from .config import config
from .models import LintFinding

logger = logging.getLogger(__name__)


def _build_prompt(query: str, lint_findings: list[LintFinding], similar_cases: list[dict]) -> str:
    """Build the structured prompt for Gemini."""

    # Format lint findings
    if lint_findings:
        findings_text = "\n".join(
            f"- [{f.severity.value}] {f.rule_name}: {f.description}"
            for f in lint_findings
        )
    else:
        findings_text = "None detected."

    # Format similar cases
    if similar_cases:
        cases_text = "\n".join(
            f"- Case '{c.get('case_id', 'unknown')}': "
            f"Problems: {', '.join(c.get('problems', []))}. "
            f"Fix: {c.get('fix', 'N/A')}. "
            f"Similarity: {c.get('similarity', 'N/A')}"
            for c in similar_cases
        )
    else:
        cases_text = "No similar past cases found."

    return f"""You are a senior database performance engineer. Analyze this SQL query and provide optimization recommendations.

## SQL Query
```sql
{query}
```

## Already Detected Issues (deterministic linter)
{findings_text}

## Similar Past Cases (from our knowledge base)
{cases_text}

## Your Task
Based on the query, the already-detected issues, and the similar past cases:
1. Suggest specific indexes (actual CREATE INDEX statements)
2. Provide a rewritten/optimized version of the query if beneficial
3. Explain what's wrong and why in plain English
4. Consider what the similar past cases tell us about effective fixes
5. Do NOT repeat the obvious issues already caught by the linter — focus on deeper insights

Return ONLY valid JSON (no markdown backticks, no extra text) with this exact structure:
{{
    "suggested_indexes": ["CREATE INDEX idx_... ON ..."],
    "rewritten_query": "SELECT ... (or null if no rewrite needed)",
    "explanation": "Plain English explanation of issues and recommendations",
    "risk_level": "HIGH or MEDIUM or LOW",
    "confidence": "HIGH or MEDIUM or LOW",
    "estimated_improvement": "e.g. 2-5x faster"
}}"""


def analyze_with_llm(
    query: str,
    lint_findings: list[LintFinding],
    similar_cases: list[dict],
) -> Optional[dict]:
    """
    Send query + context to Gemini for deep analysis.
    Returns dict with analysis results, or None on failure.
    Also returns tokens_used and response_time_ms via the dict.
    """

    # Graceful degradation: no key = no LLM
    if not config.GEMINI_API_KEY or not config.GEMINI_API_KEY.strip():
        logger.info("No GEMINI_API_KEY configured, skipping LLM analysis.")
        return None

    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        prompt = _build_prompt(query, lint_findings, similar_cases)

        start = time.time()
        response = client.models.generate_content(
            model=config.LLM_MODEL,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                max_output_tokens=config.LLM_MAX_TOKENS,
                temperature=0.2,
            ),
        )
        elapsed_ms = int((time.time() - start) * 1000)

        # Extract token usage
        tokens_used = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            tokens_used = getattr(response.usage_metadata, "total_token_count", 0)

        raw_text = (response.text or "").strip()

        # Try to parse as JSON
        result = _parse_response(raw_text)
        result["tokens_used"] = tokens_used
        result["response_time_ms"] = elapsed_ms

        return result

    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return None


def _parse_response(raw_text: str) -> dict:
    """Parse LLM response as JSON, with fallback for malformed output."""

    # Strip markdown code fences if present despite instructions
    text = raw_text
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: return the raw text as explanation
        logger.warning("LLM response was not valid JSON, using raw text as explanation.")
        return {
            "suggested_indexes": [],
            "rewritten_query": None,
            "explanation": raw_text,
            "risk_level": "MEDIUM",
            "confidence": "LOW",
            "estimated_improvement": "unknown",
        }
