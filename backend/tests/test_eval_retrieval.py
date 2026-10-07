"""
Tests for evals/eval_retrieval.py -- the retrieval eval harness.

Two separate concerns here, deliberately kept apart:

- The sweep and summary arithmetic is pure and tested on hand-built data. It
  is worth testing because the threshold decision rests on it: if
  `suppressed_wrong` were computed wrongly, the calibrated threshold would be
  wrong too, and nothing else in the suite would notice.
- One end-to-end test runs the real harness against the real golden set and
  seeded collection, as a regression gate on retrieval quality itself.
"""

import json
from pathlib import Path

import pytest

from evals.eval_retrieval import GOLDEN_PATH, describe, percentile, run, sweep_thresholds
from tests.conftest import _point_chroma, _reset_rag_globals


# --------------------------------------------------------------------------
# Summary statistics
# --------------------------------------------------------------------------

def test_percentile_of_an_empty_sample_is_none():
    assert percentile([], 0.5) is None


def test_median_of_an_odd_sample():
    assert percentile([0.1, 0.5, 0.9], 0.5) == 0.5


def test_percentile_takes_the_nearest_rank():
    assert percentile([0.0, 0.25, 0.5, 0.75, 1.0], 0.25) == 0.25


def test_percentile_is_order_independent():
    assert percentile([0.9, 0.1, 0.5], 0.5) == percentile([0.1, 0.5, 0.9], 0.5)


def test_describe_reports_the_sample_size():
    assert describe([0.1, 0.2, 0.3])["n"] == 3


def test_describe_reports_the_range():
    d = describe([0.4, 0.1, 0.9])
    assert (d["min"], d["max"]) == (0.1, 0.9)


def test_describe_of_an_empty_sample_is_all_none():
    d = describe([])
    assert d["n"] == 0 and d["min"] is None and d["mean"] is None


# --------------------------------------------------------------------------
# Threshold sweep
#
# One positive query whose correct case scores 0.70 with a wrong neighbour at
# 0.40, and one negative query whose best wrong result scores 0.45.
# --------------------------------------------------------------------------

@pytest.fixture
def hand_built():
    return [
        {
            "category": "positive",
            "hit_rank": 1,
            "retrieved": [
                {"case_id": "right", "similarity": 0.70, "correct": True},
                {"case_id": "wrong", "similarity": 0.40, "correct": False},
            ],
        },
        {
            "category": "negative",
            "hit_rank": None,
            "retrieved": [
                {"case_id": "wrong-a", "similarity": 0.45, "correct": False},
                {"case_id": "wrong-b", "similarity": 0.20, "correct": False},
            ],
        },
    ]


@pytest.fixture
def swept(hand_built):
    return {row["threshold"]: row for row in sweep_thresholds(hand_built, [0.30, 0.50, 0.80])}


def test_a_low_threshold_keeps_the_hit(swept):
    assert swept[0.30]["kept_hit_rate"] == 1.0


def test_a_low_threshold_suppresses_only_the_weakest_wrong_result(swept):
    """Of the three wrong results (0.40, 0.45, 0.20), only 0.20 is below 0.30."""
    assert swept[0.30]["suppressed_wrong"] == 1


def test_a_low_threshold_rejects_no_negative_query(swept):
    assert swept[0.30]["fully_rejected_negatives"] == 0


def test_a_mid_threshold_keeps_the_hit(swept):
    """0.50 is below the correct score of 0.70, so the hit survives."""
    assert swept[0.50]["kept_hit_rate"] == 1.0


def test_a_mid_threshold_suppresses_every_wrong_result(swept):
    """All three wrong results (0.40, 0.45, 0.20) fall below 0.50."""
    assert swept[0.50]["suppressed_wrong"] == 3


def test_a_mid_threshold_fully_rejects_the_negative_query(swept):
    assert swept[0.50]["negative_reject_rate"] == 1.0


def test_too_high_a_threshold_costs_the_hit(swept):
    """0.80 is above the correct score, so the real match is lost too."""
    assert swept[0.80]["kept_hit_rate"] == 0.0


def test_the_sweep_covers_every_requested_threshold(hand_built):
    rows = sweep_thresholds(hand_built, [0.3, 0.4, 0.5])
    assert [r["threshold"] for r in rows] == [0.3, 0.4, 0.5]


def test_kept_hit_rate_never_rises_with_the_threshold(hand_built):
    """A monotonicity check: raising the bar can only cost hit rate."""
    rates = [r["kept_hit_rate"] for r in sweep_thresholds(hand_built, [0.2, 0.4, 0.6, 0.8])]
    assert rates == sorted(rates, reverse=True)


def test_suppression_never_falls_with_the_threshold(hand_built):
    counts = [r["suppressed_wrong"] for r in sweep_thresholds(hand_built, [0.2, 0.4, 0.6, 0.8])]
    assert counts == sorted(counts)


def test_a_missed_positive_is_not_counted_as_kept(hand_built):
    """A query whose correct case was never retrieved cannot be 'kept'."""
    missed = [{"category": "positive", "hit_rank": None,
               "retrieved": [{"case_id": "wrong", "similarity": 0.9, "correct": False}]}]
    assert sweep_thresholds(missed, [0.30])[0]["kept_hits"] == 0


# --------------------------------------------------------------------------
# The golden set itself
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def golden():
    return json.loads(Path(GOLDEN_PATH).read_text(encoding="utf-8"))


def test_the_golden_set_has_positives_and_negatives(golden):
    """Without negatives a sweep cannot measure what a threshold is for."""
    categories = {c["category"] for c in golden}
    assert categories == {"positive", "negative"}


def test_golden_ids_are_unique(golden):
    ids = [c["id"] for c in golden]
    assert len(ids) == len(set(ids))


def test_every_positive_names_an_expected_case(golden):
    positives = [c for c in golden if c["category"] == "positive"]
    assert all(c["expected_case_id"] for c in positives)


def test_every_negative_expects_nothing(golden):
    negatives = [c for c in golden if c["category"] == "negative"]
    assert all(c["expected_case_id"] is None for c in negatives)


def test_every_expected_case_id_exists_in_the_seed_data(golden):
    """A typo here would look like a retrieval failure forever."""
    seed_path = Path(__file__).parent.parent / "cases" / "seed_cases.json"
    seed_ids = {c["case_id"] for c in json.loads(seed_path.read_text(encoding="utf-8"))}
    expected = {c["expected_case_id"] for c in golden if c["expected_case_id"]}
    assert expected <= seed_ids


def test_every_golden_case_explains_itself(golden):
    assert all(c.get("note") for c in golden)


# --------------------------------------------------------------------------
# End to end against the real seeded collection
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def eval_result(tmp_path_factory):
    """Seed a collection once, then run the real harness over the golden set."""
    from app import config as config_mod
    from app.seed_cases import seed

    directory = tmp_path_factory.mktemp("retrieval_eval_chroma")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_mod.config, "CHROMA_PERSIST_DIR", str(directory))
        _reset_rag_globals()
        seed()
        result = run(GOLDEN_PATH, top_k=3)
    _reset_rag_globals()
    return result


def test_hit_rate_at_3_is_perfect(eval_result):
    """The gate CI enforces. A drop means retrieval regressed."""
    assert eval_result["hit_rate_at_3"] == 1.0


def test_hit_rate_at_1_is_reported(eval_result):
    assert 0 <= eval_result["hit_rate_at_1"] <= 1


def test_correct_matches_score_above_the_configured_threshold(eval_result):
    """
    The calibration claim: every correct match clears the threshold. If this
    fails, RAG_MIN_SIMILARITY is now suppressing genuine matches.
    """
    from app.config import config

    assert eval_result["distribution"]["correct"]["min"] >= config.RAG_MIN_SIMILARITY


def test_no_query_without_a_precedent_clears_the_threshold(eval_result):
    """
    The other half of the calibration, and the guarantee that fixes the
    original bug: no negative query produces a single result above the
    threshold. The margin is thin (best negative 0.480 vs threshold 0.5), so
    this is the test that catches a seed-data or embedding change eroding it.

    Note this is deliberately not "no incorrect result ever clears 0.5".
    Several do, but only ever beside a correct match -- a query about UPPER()
    also retrieves the EXTRACT() and LOWER() cases, which are genuinely
    similar and merely not the one the golden set labels. That is a limit of
    single-ground-truth labelling, not a threshold failure.
    """
    from app.config import config

    negatives = [q for q in eval_result["per_query"] if q["category"] == "negative"]
    worst = max(q["top_similarity"] for q in negatives)
    assert worst < config.RAG_MIN_SIMILARITY


def test_wrong_results_above_the_threshold_only_accompany_a_correct_one(eval_result):
    """
    Pins the reasoning above: if a wrong result ever cleared the threshold on a
    query that retrieved no correct case, the threshold really would be
    leaking and this would fail.
    """
    from app.config import config

    leaking = [
        q["id"]
        for q in eval_result["per_query"]
        if q["hit_rank"] is None
        and any(h["similarity"] >= config.RAG_MIN_SIMILARITY for h in q["retrieved"])
    ]
    assert leaking == []


def test_every_negative_query_is_fully_rejected(eval_result):
    """No negative query may produce a single result above the threshold."""
    from app.config import config

    row = next(r for r in eval_result["sweep"] if r["threshold"] == config.RAG_MIN_SIMILARITY)
    assert row["negative_reject_rate"] == 1.0


def test_the_configured_threshold_costs_no_hit_rate(eval_result):
    """0.5 was chosen as the highest threshold that keeps hit rate at 100%."""
    from app.config import config

    row = next(r for r in eval_result["sweep"] if r["threshold"] == config.RAG_MIN_SIMILARITY)
    assert row["kept_hit_rate"] == 1.0


def test_the_slow_scan_query_matches_nothing(eval_result):
    """The query from the original bug report."""
    from app.config import config

    q = next(q for q in eval_result["per_query"] if q["id"] == "neg-slow-scan")
    assert q["top_similarity"] < config.RAG_MIN_SIMILARITY
