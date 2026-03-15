"""
Tests for api.py — FastAPI REST endpoints.
Run: uv run python -m pytest tests/test_api.py
"""

import os
import sys

# Use a test database
os.environ["SQLITE_DB_PATH"] = "./test_api.db"

from app import config as config_mod
config_mod.config.SQLITE_DB_PATH = "./test_api.db"

from fastapi.testclient import TestClient

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def run_tests():
    # Clean up from previous runs
    for f in ("./test_api.db",):
        if os.path.exists(f):
            os.remove(f)

    # Initialize pipeline before creating client
    from app import pipeline
    pipeline.init()

    from app.api import app
    client = TestClient(app, raise_server_exceptions=False)

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

    print(f"\n{BOLD}API Tests{RESET}\n")

    # --- POST /analyze ---
    print(f"{BOLD}POST /analyze{RESET}")
    resp = client.post("/analyze", json={"query": "SELECT * FROM users;"})
    check("returns 200", resp.status_code == 200)
    data = resp.json()
    check("has lint_findings", len(data["lint_findings"]) > 0)
    check("detects SELECT_STAR", any(f["rule_name"] == "SELECT_STAR" for f in data["lint_findings"]))
    check("has overall_severity", "overall_severity" in data)
    check("has summary", "summary" in data)
    check("has response_time_ms", "response_time_ms" in data)
    check("has similar_cases list", isinstance(data.get("similar_cases"), list))

    # Analyze a clean query
    resp2 = client.post("/analyze", json={"query": "SELECT id, name FROM users WHERE id = 1 LIMIT 10;"})
    check("clean query returns 200", resp2.status_code == 200)
    data2 = resp2.json()
    check("clean query has 0 findings", len(data2["lint_findings"]) == 0)

    # Missing body
    resp3 = client.post("/analyze", json={})
    check("missing query returns 422", resp3.status_code == 422)

    # --- POST /feedback ---
    print(f"\n{BOLD}POST /feedback{RESET}")

    # First get a valid analysis_id from recent
    recent_resp = client.get("/recent?limit=1")
    analysis_id = recent_resp.json()[0]["id"]

    resp4 = client.post("/feedback", json={
        "analysis_id": analysis_id,
        "accepted": True,
        "comments": "good suggestion"
    })
    check("feedback returns 200", resp4.status_code == 200)
    check("feedback status is ok", resp4.json()["status"] == "ok")

    # Feedback with accepted=False
    resp5 = client.post("/feedback", json={
        "analysis_id": analysis_id,
        "accepted": False,
        "comments": "not helpful"
    })
    check("rejected feedback returns 200", resp5.status_code == 200)

    # Missing fields
    resp6 = client.post("/feedback", json={"analysis_id": 1})
    check("missing accepted returns 422", resp6.status_code == 422)

    # --- GET /metrics ---
    print(f"\n{BOLD}GET /metrics{RESET}")
    resp7 = client.get("/metrics")
    check("metrics returns 200", resp7.status_code == 200)
    metrics = resp7.json()
    check("has total_analyses", "total_analyses" in metrics)
    check("total_analyses >= 2", metrics["total_analyses"] >= 2)
    check("has acceptance_rate", "acceptance_rate" in metrics)
    check("has rule_counts", "rule_counts" in metrics)

    # --- GET /recent ---
    print(f"\n{BOLD}GET /recent{RESET}")
    resp8 = client.get("/recent")
    check("recent returns 200", resp8.status_code == 200)
    recent = resp8.json()
    check("recent is a list", isinstance(recent, list))
    check("recent has entries", len(recent) > 0)
    check("entries have id", "id" in recent[0])
    check("entries have query", "query" in recent[0])

    # With limit param
    resp9 = client.get("/recent?limit=1")
    check("limit=1 returns 1 entry", len(resp9.json()) == 1)

    # Invalid limit
    resp10 = client.get("/recent?limit=0")
    check("limit=0 returns 422", resp10.status_code == 422)

    # --- GET /health ---
    print(f"\n{BOLD}GET /health{RESET}")
    resp11 = client.get("/health")
    check("health returns 200", resp11.status_code == 200)
    health = resp11.json()
    check("status is healthy", health["status"] == "healthy")
    check("has rag_cases", "rag_cases" in health)
    check("rag_cases >= 0", health["rag_cases"] >= 0)
    check("db is connected", health["db"] == "connected")

    # --- Summary ---
    print(f"\n{'-'*40}")
    print(f"  {GREEN}Passed: {passed}{RESET}  {RED}Failed: {failed}{RESET}")
    print(f"{'-'*40}\n")

    # Cleanup
    try:
        os.remove("./test_api.db")
    except OSError:
        pass

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
