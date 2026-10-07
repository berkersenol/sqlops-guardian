"""
Tests for the seed case data in cases/seed_cases.json.

These are data-correctness tests, not retrieval tests: they read the JSON
directly and need neither ChromaDB nor an embedding model. They exist because
a seed case is advice that reaches a user -- it is fed to the LLM as a
precedent and shown in the UI as "this is what worked" -- so a case that
recommends a wrong fix is a bug in the product, not just bad test data.
"""

import json
from pathlib import Path

import pytest

CASES_PATH = Path(__file__).parent.parent / "cases" / "seed_cases.json"
REQUIRED_KEYS = {"case_id", "query", "problems", "fix", "tables"}


@pytest.fixture(scope="module")
def cases():
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_id(cases):
    return {c["case_id"]: c for c in cases}


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

def test_the_seed_file_is_not_empty(cases):
    assert len(cases) > 0


def test_every_case_has_the_required_keys(cases):
    assert all(REQUIRED_KEYS <= set(c) for c in cases)


def test_case_ids_are_unique(cases):
    ids = [c["case_id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_every_case_declares_at_least_one_problem(cases):
    assert all(c["problems"] for c in cases)


def test_every_case_has_a_non_empty_fix(cases):
    assert all(c["fix"].strip() for c in cases)


def test_every_case_names_at_least_one_table(cases):
    assert all(c["tables"] for c in cases)


# --------------------------------------------------------------------------
# or-across-columns: the UNION ALL regression
#
# The fix originally recommended UNION ALL. A row matching both branches --
# an Electronics product made by Apple -- appears in both, so UNION ALL
# returns it twice, while the OR it replaces returns it once. The recommended
# rewrite silently changed the result set.
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def or_case(by_id):
    return by_id["or-across-columns"]


def test_or_case_does_not_recommend_union_all(or_case):
    """The rewrite itself must not be a UNION ALL."""
    assert "UNION ALL SELECT" not in or_case["fix"]


def test_or_case_recommends_a_deduplicating_union(or_case):
    assert "UNION SELECT" in or_case["fix"]


def test_or_case_explains_the_duplicate_problem(or_case):
    """A fix the reader cannot evaluate is barely better than a wrong one."""
    assert "duplicate" in or_case["fix"].lower()


def test_or_case_explains_when_union_all_would_be_correct(or_case):
    assert "disjoint" in or_case["fix"].lower()


def test_or_case_still_explains_the_index_motivation(or_case):
    """The split exists for index use; that reason must survive the fix."""
    assert "index" in or_case["fix"].lower()


def test_or_case_rewrite_still_targets_the_original_columns(or_case):
    assert "category" in or_case["fix"] and "brand" in or_case["fix"]


# --------------------------------------------------------------------------
# No other case recommends a rewrite that changes row multiplicity
# --------------------------------------------------------------------------

def test_no_case_recommends_union_all(cases):
    """
    UNION ALL is only safe when the branches are provably disjoint, which no
    current case establishes. If a future case genuinely needs it, this test
    should be updated deliberately rather than silently.
    """
    offenders = [c["case_id"] for c in cases if "UNION ALL SELECT" in c["fix"]]
    assert offenders == []
