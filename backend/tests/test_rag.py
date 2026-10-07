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


# --------------------------------------------------------------------------
# Similarity threshold / low-confidence flagging
#
# A vector search returns its n nearest neighbours however distant, so an
# unrelated query still comes back with a full set of results. search_similar
# flags rather than drops them: callers decide whether to show a weak match,
# and the eval harness needs the raw distribution to calibrate against.
# The threshold itself is calibrated in evals/eval_retrieval.py.
# --------------------------------------------------------------------------

IRRELEVANT_QUERY = "slow scan"


def test_every_result_carries_a_low_confidence_flag(seeded_collection):
    results = search_similar("SELECT * FROM orders;", ["SELECT_STAR"], n_results=3)
    assert all("low_confidence" in r for r in results)


def test_a_close_match_is_not_flagged_low_confidence(seeded_collection):
    """The seed query itself must clear the threshold comfortably."""
    results = search_similar(
        "SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;",
        ["FUNCTION_ON_COLUMN", "SELECT_STAR"],
        n_results=1,
    )
    assert results[0]["low_confidence"] is False


def test_an_irrelevant_query_is_flagged_low_confidence(seeded_collection):
    """The original bug: 'slow scan' returned unrelated cases as matches."""
    results = search_similar(IRRELEVANT_QUERY, n_results=3)
    assert all(r["low_confidence"] for r in results)


def test_an_irrelevant_query_still_returns_the_neighbours(seeded_collection):
    """Flagged, not dropped -- the caller chooses what to do with them."""
    assert len(search_similar(IRRELEVANT_QUERY, n_results=3)) == 3


def test_min_similarity_zero_flags_nothing(seeded_collection):
    """How the eval harness reads the raw distribution."""
    results = search_similar(IRRELEVANT_QUERY, n_results=3, min_similarity=0.0)
    assert not any(r["low_confidence"] for r in results)


def test_min_similarity_one_flags_everything(seeded_collection):
    results = search_similar(
        "SELECT * FROM orders;", ["SELECT_STAR"], n_results=3, min_similarity=1.0
    )
    assert all(r["low_confidence"] for r in results)


def test_min_similarity_argument_overrides_the_configured_default(seeded_collection):
    """An explicit threshold must win over config.RAG_MIN_SIMILARITY."""
    strict = search_similar(IRRELEVANT_QUERY, n_results=1, min_similarity=0.99)
    loose = search_similar(IRRELEVANT_QUERY, n_results=1, min_similarity=0.01)
    assert strict[0]["low_confidence"] and not loose[0]["low_confidence"]


def test_the_flag_follows_the_configured_threshold(seeded_collection, monkeypatch):
    from app import config as config_mod

    query = "SELECT * FROM orders WHERE EXTRACT(YEAR FROM created_at) = 2025;"
    problems = ["FUNCTION_ON_COLUMN", "SELECT_STAR"]

    monkeypatch.setattr(config_mod.config, "RAG_MIN_SIMILARITY", 0.99)
    assert search_similar(query, problems, n_results=1)[0]["low_confidence"] is True


def test_the_flag_is_consistent_with_the_reported_similarity(seeded_collection):
    """Guards against the flag and the score drifting apart."""
    from app.config import config

    results = search_similar("SELECT * FROM orders;", ["SELECT_STAR"], n_results=5)
    assert all(
        r["low_confidence"] == (r["similarity"] < config.RAG_MIN_SIMILARITY)
        for r in results
    )
