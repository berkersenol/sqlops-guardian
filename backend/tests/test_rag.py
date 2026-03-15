"""
Tests for rag.py and seed_cases.py — ChromaDB RAG layer.
Run: uv run python -m pytest tests/test_rag.py
"""

import os
import shutil
import atexit

# Use a temporary ChromaDB directory for tests
os.environ["CHROMA_PERSIST_DIR"] = "./test_chroma_db"

from app import rag
from app.rag import init_collection, add_case, search_similar, get_case_count
from app.seed_cases import seed

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _reset_collection():
    """Delete and recreate the collection (avoids Windows file lock issues)."""
    if rag._client is not None:
        rag._client.delete_collection(rag._collection.name)
        rag._collection = None
        init_collection()


def _final_cleanup():
    """Best-effort removal of test ChromaDB dir at process exit."""
    rag._client = None
    rag._collection = None
    try:
        if os.path.exists("./test_chroma_db"):
            shutil.rmtree("./test_chroma_db")
    except PermissionError:
        pass  # Windows may still hold locks at exit


atexit.register(_final_cleanup)


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

    print(f"\n{BOLD}RAG Tests{RESET}\n")

    # Start fresh — delete any leftover collection from previous runs
    col = init_collection()
    if col.count() > 0:
        rag._client.delete_collection(col.name)
        rag._collection = None
        col = init_collection()
    check("init_collection returns collection", col is not None)

    # Test 2: empty collection
    check("empty collection has 0 cases", get_case_count() == 0)

    # Test 3: add a single case
    add_case(
        case_id="test-1",
        query="SELECT * FROM users;",
        problems=["SELECT_STAR"],
        fix="Listed specific columns",
        tables=["users"],
    )
    check("add_case stores 1 case", get_case_count() == 1)

    # Test 4: upsert same id doesn't duplicate
    add_case(
        case_id="test-1",
        query="SELECT * FROM users;",
        problems=["SELECT_STAR"],
        fix="Listed specific columns",
        tables=["users"],
    )
    check("upsert same id keeps count at 1", get_case_count() == 1)

    # Test 5: search returns results
    results = search_similar("SELECT * FROM customers;", ["SELECT_STAR"])
    check("search returns results", len(results) > 0)
    check("result has case_id", results[0]["case_id"] == "test-1")
    check("result has similarity score", results[0]["similarity"] is not None)

    # Reset collection for seed tests
    _reset_collection()

    # Test 6: seed_cases populates ChromaDB
    count = seed()
    check("seed loads 15 cases", count == 15)
    check("get_case_count matches", get_case_count() == 15)

    # Test 7: SARGability query matches SARGability cases
    results = search_similar(
        "SELECT id FROM invoices WHERE EXTRACT(MONTH FROM due_date) = 3;",
        ["FUNCTION_ON_COLUMN"],
        n_results=3,
    )
    top_ids = [r["case_id"] for r in results]
    sarg_match = any("sarg" in cid for cid in top_ids)
    check("SARGability query matches sarg cases in top 3", sarg_match)

    # Test 8: DELETE without WHERE matches delete case
    results = search_similar(
        "DELETE FROM temp_data;",
        ["DELETE_WITHOUT_WHERE"],
        n_results=3,
    )
    top_ids = [r["case_id"] for r in results]
    delete_match = any("delete" in cid for cid in top_ids)
    check("DELETE query matches delete case in top 3", delete_match)

    # Test 9: LEFT JOIN WHERE trap matches
    results = search_similar(
        "SELECT u.name, p.title FROM users u LEFT JOIN posts p ON u.id = p.user_id WHERE p.published = true;",
        ["LEFT_JOIN_WHERE_TRAP"],
        n_results=3,
    )
    top_ids = [r["case_id"] for r in results]
    lj_match = any("left-join" in cid for cid in top_ids)
    check("LEFT JOIN WHERE query matches left-join cases in top 3", lj_match)

    # Test 10: results are sorted by similarity (highest first)
    results = search_similar("SELECT * FROM orders;", ["SELECT_STAR"], n_results=5)
    similarities = [r["similarity"] for r in results]
    check("results sorted by similarity descending", similarities == sorted(similarities, reverse=True))

    # Test 11: search with n_results parameter
    results = search_similar("SELECT * FROM orders;", n_results=2)
    check("n_results=2 returns at most 2", len(results) <= 2)

    # Test 12: result metadata is intact
    results = search_similar(
        "UPDATE users SET active = false;",
        ["UPDATE_WITHOUT_WHERE"],
        n_results=1,
    )
    r = results[0]
    check("result has fix field", len(r["fix"]) > 0)
    check("result has tables list", isinstance(r["tables"], list))
    check("result has problems list", isinstance(r["problems"], list))

    print(f"\n{'-'*40}")
    print(f"  {GREEN}Passed: {passed}{RESET}  {RED}Failed: {failed}{RESET}")
    print(f"{'-'*40}\n")

    return failed == 0


if __name__ == "__main__":
    run_tests()
