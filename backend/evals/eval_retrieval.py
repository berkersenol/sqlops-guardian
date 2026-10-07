"""
Retrieval evaluation: measures whether RAG search returns the right seed case,
and calibrates the similarity threshold below which a result should not be
presented as a match.

Why this exists: a vector search always returns its n nearest neighbours, no
matter how far away they are. Searching for "slow scan" returned three cases at
~0.43 similarity, none of them relevant, presented exactly like a real match.
A threshold fixes that, but only if it is chosen from the actual score
distribution rather than guessed.

The golden set is therefore half positives -- a query plus the seed case_id
that should be retrieved -- and half negatives, queries with no legitimate
precedent at all. Negatives are what make the sweep meaningful: with positives
only, every threshold below the lowest correct score scores perfectly, and the
data would always favour a threshold of 0.

Usage (from backend/):
    python -m evals.eval_retrieval                    # report + JSON to evals/results/
    python -m evals.eval_retrieval --query-only      # without the lint rule names
    python -m evals.eval_retrieval --min-hit-rate 1.0    # exit 1 below that (for CI)
    python -m evals.eval_retrieval --top-k 5
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.linter import lint_sql
from app.rag import get_case_count, search_similar

EVAL_DIR = Path(__file__).parent
GOLDEN_PATH = EVAL_DIR / "golden_retrieval.json"
RESULTS_DIR = EVAL_DIR / "results"

# Thresholds to sweep. 0.05 steps are fine enough to find the gap between the
# correct and incorrect score distributions without implying false precision.
SWEEP = [round(0.30 + 0.05 * i, 2) for i in range(11)]


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def percentile(xs: list[float], p: float) -> float | None:
    """Nearest-rank percentile. The sample is ~20 values, so interpolating
    between neighbours would imply precision the sample size cannot support."""
    if not xs:
        return None
    ordered = sorted(xs)
    idx = max(0, min(len(ordered) - 1, round(p * (len(ordered) - 1))))
    return ordered[idx]


def fmt(x: float | None) -> str:
    return "  n/a" if x is None else f"{x:.3f}"


def describe(xs: list[float]) -> dict:
    return {
        "n": len(xs),
        "min": min(xs) if xs else None,
        "p25": percentile(xs, 0.25),
        "median": percentile(xs, 0.50),
        "p75": percentile(xs, 0.75),
        "max": max(xs) if xs else None,
        "mean": mean(xs),
    }


def run(golden: Path = GOLDEN_PATH, top_k: int = 3, query_only: bool = False) -> dict:
    cases = json.loads(golden.read_text(encoding="utf-8"))

    # An unseeded collection returns nothing for every query, which would
    # otherwise look like a total retrieval failure rather than a setup
    # mistake. This is the normal state of a fresh checkout or a CI runner.
    if get_case_count() == 0:
        raise SystemExit(
            "ChromaDB holds no cases, so every query would score zero.\n"
            "Seed it first:  uv run python -m app.seed_cases"
        )

    per_query = []
    # Every similarity score seen, split by whether the result was the case the
    # query was supposed to find. These two distributions are what the
    # threshold has to separate.
    correct_scores: list[float] = []
    incorrect_scores: list[float] = []

    for case in cases:
        expected = case["expected_case_id"]

        # Mirror how callers build their search text. Both the MCP tool and
        # pipeline.analyze pass the lint rule names alongside the query, which
        # is what aligns it with the indexed "Problems:" field. --query-only
        # reproduces the weaker shape the MCP tool used before calibration, so
        # the difference stays measurable rather than anecdotal.
        problems = None if query_only else [f.rule_name for f in lint_sql(case["query"])]

        # min_similarity=0.0 so nothing is flagged: this harness needs the raw
        # distribution, which is the thing being calibrated.
        hits = search_similar(
            case["query"], problems=problems, n_results=top_k, min_similarity=0.0
        )

        retrieved = [
            {
                "case_id": h["case_id"],
                "similarity": h["similarity"],
                "correct": h["case_id"] == expected,
            }
            for h in hits
        ]
        for h in retrieved:
            if h["similarity"] is None:
                continue
            (correct_scores if h["correct"] else incorrect_scores).append(h["similarity"])

        rank = next((i + 1 for i, h in enumerate(retrieved) if h["correct"]), None)
        per_query.append({
            "id": case["id"],
            "category": case["category"],
            "query": case["query"],
            "expected_case_id": expected,
            "retrieved": retrieved,
            "hit_rank": rank,
            "top_similarity": retrieved[0]["similarity"] if retrieved else None,
            "note": case.get("note", ""),
        })

    positives = [q for q in per_query if q["category"] == "positive"]
    negatives = [q for q in per_query if q["category"] == "negative"]

    hit_at_1 = sum(1 for q in positives if q["hit_rank"] == 1)
    hit_at_k = sum(1 for q in positives if q["hit_rank"] is not None)

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "golden_set": golden.name,
        "top_k": top_k,
        "search_shape": "query only" if query_only else "query+problems",
        "n_positive": len(positives),
        "n_negative": len(negatives),
        "hit_rate_at_1": hit_at_1 / len(positives) if positives else None,
        f"hit_rate_at_{top_k}": hit_at_k / len(positives) if positives else None,
        "distribution": {
            "correct": describe(correct_scores),
            "incorrect": describe(incorrect_scores),
        },
        "sweep": sweep_thresholds(per_query, SWEEP),
        "per_query": per_query,
    }


def sweep_thresholds(per_query: list[dict], thresholds: list[float]) -> list[dict]:
    """For each candidate threshold, what survives and what gets suppressed.

    Two numbers decide the threshold:

    - kept_hit_rate: of the positive queries, the fraction that still retrieve
      their expected case *above* the threshold. Raising the threshold can only
      lower this. This is the cost.
    - suppressed_wrong: of every incorrect result returned across all queries,
      the fraction that falls below the threshold and so stops being presented
      as a match. Raising the threshold can only raise this. This is the gain.

    The right threshold is the highest one that has not yet started costing
    hit rate.
    """
    rows = []
    positives = [q for q in per_query if q["category"] == "positive"]
    wrong = [
        h["similarity"]
        for q in per_query
        for h in q["retrieved"]
        if not h["correct"] and h["similarity"] is not None
    ]
    # A negative query is clean when nothing it returned survives the
    # threshold -- the user sees "no close matches" instead of a false match.
    negatives = [q for q in per_query if q["category"] == "negative"]

    for t in thresholds:
        kept = sum(
            1
            for q in positives
            if q["hit_rank"] is not None
            and q["retrieved"][q["hit_rank"] - 1]["similarity"] >= t
        )
        suppressed = sum(1 for s in wrong if s < t)
        clean_negatives = sum(
            1
            for q in negatives
            if all(
                h["similarity"] is None or h["similarity"] < t for h in q["retrieved"]
            )
        )
        rows.append({
            "threshold": t,
            "kept_hits": kept,
            "kept_hit_rate": kept / len(positives) if positives else None,
            "suppressed_wrong": suppressed,
            "suppressed_wrong_rate": suppressed / len(wrong) if wrong else None,
            "fully_rejected_negatives": clean_negatives,
            "negative_reject_rate": clean_negatives / len(negatives) if negatives else None,
        })
    return rows


def print_report(res: dict) -> None:
    k = res["top_k"]
    print(f"\nRetrieval eval on {res['n_positive']} positive + {res['n_negative']} "
          f"negative queries  (top_k={k}, search shape: {res['search_shape']})")
    print(f"hit rate@1  {res['hit_rate_at_1']:.0%}"
          f"     hit rate@{k}  {res[f'hit_rate_at_{k}']:.0%}\n")

    print("Similarity distribution")
    print(f"{'':<12}{'n':>4}{'min':>8}{'p25':>8}{'median':>8}{'p75':>8}{'max':>8}{'mean':>8}")
    for label in ("correct", "incorrect"):
        d = res["distribution"][label]
        print(f"{label:<12}{d['n']:>4}{fmt(d['min']):>8}{fmt(d['p25']):>8}"
              f"{fmt(d['median']):>8}{fmt(d['p75']):>8}{fmt(d['max']):>8}{fmt(d['mean']):>8}")

    print("\nThreshold sweep")
    print(f"{'thresh':>7}{'hit rate@'+str(k):>13}{'wrong suppressed':>19}"
          f"{'negatives rejected':>21}")
    for row in res["sweep"]:
        print(f"{row['threshold']:>7.2f}"
              f"{row['kept_hits']:>7}/{res['n_positive']:<2} "
              f"({row['kept_hit_rate']:.0%})"
              f"{row['suppressed_wrong']:>11}/{res['distribution']['incorrect']['n']:<3}"
              f" ({row['suppressed_wrong_rate']:.0%})"
              f"{row['fully_rejected_negatives']:>11}/{res['n_negative']:<2}"
              f" ({row['negative_reject_rate']:.0%})")

    misses = [q for q in res["per_query"] if q["category"] == "positive" and q["hit_rank"] is None]
    if misses:
        print(f"\nMissed (expected case not in top {k}):")
        for q in misses:
            got = ", ".join(f"{h['case_id']} {h['similarity']:.2f}" for h in q["retrieved"])
            print(f"  {q['id']}  expected {q['expected_case_id']}")
            print(f"      got: {got}")

    print("\nTop match per negative query (all of these should fall below the threshold):")
    for q in res["per_query"]:
        if q["category"] == "negative":
            top = q["retrieved"][0] if q["retrieved"] else None
            shown = f"{top['case_id']} {top['similarity']:.3f}" if top else "nothing"
            print(f"  {q['id']:<22} {shown}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH,
                        help=f"Golden set to evaluate (default: {GOLDEN_PATH.name})")
    parser.add_argument("--top-k", type=int, default=3,
                        help="Neighbours to retrieve per query (default: 3)")
    parser.add_argument("--query-only", action="store_true",
                        help="Search on the query alone, omitting the lint rule names callers pass")
    parser.add_argument("--min-hit-rate", type=float, default=None,
                        help="Fail (exit 1) if hit rate@top_k is below this value")
    args = parser.parse_args()

    if not args.golden.is_file():
        raise SystemExit(f"Golden set not found: {args.golden}")

    res = run(args.golden, top_k=args.top_k, query_only=args.query_only)
    print_report(res)

    RESULTS_DIR.mkdir(exist_ok=True)
    suffix = "_query_only" if args.query_only else ""
    out = RESULTS_DIR / f"retrieval_latest{suffix}.json"
    out.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(EVAL_DIR.parent)}")

    hit_rate = res[f"hit_rate_at_{args.top_k}"] or 0
    if args.min_hit_rate is not None and hit_rate < args.min_hit_rate:
        raise SystemExit(
            f"hit rate@{args.top_k} {hit_rate:.2f} is below threshold {args.min_hit_rate}"
        )


if __name__ == "__main__":
    main()
