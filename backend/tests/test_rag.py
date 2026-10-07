"""
Tests for rag.py and seed_cases.py — ChromaDB RAG layer.

Converted from a hand-rolled script. The original walked one shared collection
through a fixed sequence and wrote to ./test_chroma_db in the repo, with an
atexit hook papering over Windows file locks. Now:

- `empty_collection` gives the empty-state tests their own tmp directory.
- `seeded_collection` seeds the 15 canned cases once per module (embedding is
  the slow part) and re-points the rag singletons before each test that needs
  them, so tests no longer depend on declaration order.
"""

import pytest

from app.rag import add_case, get_case_count, search_similar

CASE = dict(
    case_id="test-1",
    query="SELECT * FROM users;",
    problems=["SELECT_STAR"],
    fix="Listed specific columns",
    tables=["users"],
)


# --------------------------------------------------------------------------
# Empty collection
# --------------------------------------------------------------------------

def test_init_collection_returns_collection(empty_collection):
    assert empty_collection is not None


def test_empty_collection_has_no_cases(empty_collection):
    assert get_case_count() == 0


def test_add_case_stores_one_case(empty_collection):
    add_case(**CASE)
    assert get_case_count() == 1


def test_upsert_same_id_does_not_duplicate(empty_collection):
    add_case(**CASE)
    add_case(**CASE)
    assert get_case_count() == 1


# --------------------------------------------------------------------------
# Search against a single known case
# --------------------------------------------------------------------------

@pytest.fixture
def one_case(empty_collection):
    add_case(**CASE)
    return search_similar("SELECT * FROM customers;", ["SELECT_STAR"])


def test_search_returns_results(one_case):
    assert len(one_case) > 0


def test_search_result_has_case_id(one_case):
    assert one_case[0]["case_id"] == "test-1"


def test_search_result_has_similarity_score(one_case):
    assert one_case[0]["similarity"] is not None


# --------------------------------------------------------------------------
# Seeded collection
# --------------------------------------------------------------------------

def test_seed_loads_fifteen_cases(seeded_collection):
    assert seeded_collection == 15


def test_case_count_matches_seed(seeded_collection):
    assert get_case_count() == 15


@pytest.mark.parametrize(
    "query, problems, expected_substring",
    [
        pytest.param(
            "SELECT id FROM invoices WHERE EXTRACT(MONTH FROM due_date) = 3;",
            ["FUNCTION_ON_COLUMN"],
            "sarg",
            id="sargability",
        ),
        pytest.param(
            "DELETE FROM temp_data;",
            ["DELETE_WITHOUT_WHERE"],
            "delete",
            id="delete-without-where",
        ),
        pytest.param(
            "SELECT u.name, p.title FROM users u LEFT JOIN posts p "
            "ON u.id = p.user_id WHERE p.published = true;",
            ["LEFT_JOIN_WHERE_TRAP"],
            "left-join",
            id="left-join-trap",
        ),
    ],
)
def test_semantic_search_finds_the_matching_case_in_top_three(
    seeded_collection, query, problems, expected_substring
):
    results = search_similar(query, problems, n_results=3)
    top_ids = [r["case_id"] for r in results]
    assert any(expected_substring in cid for cid in top_ids), top_ids


def test_results_are_sorted_by_similarity_descending(seeded_collection):
    sims = [r["similarity"] for r in search_similar("SELECT * FROM orders;", ["SELECT_STAR"], n_results=5)]
    assert sims == sorted(sims, reverse=True)


def test_n_results_caps_the_result_count(seeded_collection):
    assert len(search_similar("SELECT * FROM orders;", n_results=2)) <= 2


@pytest.fixture
def update_case(seeded_collection):
    return search_similar(
        "UPDATE users SET active = false;",
        ["UPDATE_WITHOUT_WHERE"],
        n_results=1,
    )[0]


def test_result_has_fix_field(update_case):
    assert len(update_case["fix"]) > 0


def test_result_has_tables_list(update_case):
    assert isinstance(update_case["tables"], list)


def test_result_has_problems_list(update_case):
    assert isinstance(update_case["problems"], list)
