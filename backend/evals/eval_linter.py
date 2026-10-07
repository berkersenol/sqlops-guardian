"""
Linter evaluation: runs the deterministic linter against a labeled golden set
and reports precision / recall per rule and per category.

Usage (from backend/):
    python -m evals.eval_linter            # print report, save JSON to evals/results/
    python -m evals.eval_linter --min-f1 0.8   # exit 1 if overall F1 drops below 0.8 (for CI)
    python -m evals.eval_linter --golden evals/golden_linter_holdout.json
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.linter import ALL_RULES, lint_sql

EVAL_DIR = Path(__file__).parent
GOLDEN_PATH = EVAL_DIR / "golden_linter.json"
RESULTS_DIR = EVAL_DIR / "results"


def ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def f1(p: float | None, r: float | None) -> float | None:
    if p is None or r is None or p + r == 0:
        return None
    return 2 * p * r / (p + r)


def fmt(x: float | None) -> str:
    return " n/a" if x is None else f"{x:.2f}"


def result_path_for(golden: Path) -> Path:
    """
    Name the output after the golden file so one set does not overwrite
    another's result. The default set keeps its historical filename.
    """
    stem = golden.stem
    if stem == "golden_linter":
        return RESULTS_DIR / "linter_latest.json"
    return RESULTS_DIR / f"linter_latest_{stem.replace('golden_linter_', '')}.json"


def run(golden: Path = GOLDEN_PATH) -> dict:
    cases = json.loads(golden.read_text())
    per_rule = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    per_category = defaultdict(lambda: {"total": 0, "exact": 0})
    mismatches = []

    for case in cases:
        expected = set(case["expected_rules"])
        found = {f.rule_name for f in lint_sql(case["query"])}

        for rule in found & expected:
            per_rule[rule]["tp"] += 1
        for rule in found - expected:
            per_rule[rule]["fp"] += 1
        for rule in expected - found:
            per_rule[rule]["fn"] += 1

        cat = per_category[case["category"]]
        cat["total"] += 1
        if found == expected:
            cat["exact"] += 1
        else:
            mismatches.append({
                "id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "false_positives": sorted(found - expected),
                "false_negatives": sorted(expected - found),
                "note": case.get("note", ""),
            })

    tp = sum(r["tp"] for r in per_rule.values())
    fp = sum(r["fp"] for r in per_rule.values())
    fn = sum(r["fn"] for r in per_rule.values())
    precision, recall = ratio(tp, tp + fp), ratio(tp, tp + fn)

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "golden_set": golden.name,
        "n_cases": len(cases),
        "overall": {"precision": precision, "recall": recall,
                    "f1": f1(precision, recall), "tp": tp, "fp": fp, "fn": fn},
        "per_rule": {
            name: {**c, "precision": ratio(c["tp"], c["tp"] + c["fp"]),
                   "recall": ratio(c["tp"], c["tp"] + c["fn"])}
            for name, c in sorted(per_rule.items())
        },
        "per_category": {k: {**v, "exact_match_rate": v["exact"] / v["total"]}
                         for k, v in per_category.items()},
        "mismatches": mismatches,
    }


def print_report(res: dict) -> None:
    o = res["overall"]
    print(f"\nLinter eval on {res['n_cases']} golden cases")
    print(f"Overall  precision {fmt(o['precision'])}  recall {fmt(o['recall'])}  "
          f"F1 {fmt(o['f1'])}   (TP {o['tp']}, FP {o['fp']}, FN {o['fn']})\n")

    print(f"{'Rule':<24}{'TP':>4}{'FP':>4}{'FN':>4}{'Prec':>7}{'Rec':>7}")
    for name, r in res["per_rule"].items():
        print(f"{name:<24}{r['tp']:>4}{r['fp']:>4}{r['fn']:>4}"
              f"{fmt(r['precision']):>7}{fmt(r['recall']):>7}")

    print(f"\n{'Category':<10}{'Exact match':>14}")
    for name, c in res["per_category"].items():
        print(f"{name:<10}{c['exact']:>8}/{c['total']:<3} ({c['exact_match_rate']:.0%})")

    if res["mismatches"]:
        print("\nMismatches:")
        for m in res["mismatches"]:
            print(f"  [{m['category']}] {m['id']}")
            if m["false_positives"]:
                print(f"      false positive: {', '.join(m['false_positives'])}")
            if m["false_negatives"]:
                print(f"      missed:         {', '.join(m['false_negatives'])}")
            if m["note"]:
                print(f"      why it matters: {m['note']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-f1", type=float, default=None,
                        help="Fail (exit 1) if overall F1 is below this value")
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH,
                        help=f"Golden set to evaluate against (default: {GOLDEN_PATH.name})")
    args = parser.parse_args()

    if not args.golden.is_file():
        raise SystemExit(f"Golden set not found: {args.golden}")

    res = run(args.golden)
    print_report(res)

    RESULTS_DIR.mkdir(exist_ok=True)
    out = result_path_for(args.golden)
    out.write_text(json.dumps(res, indent=2))
    print(f"\nSaved {out.relative_to(EVAL_DIR.parent)}")

    if args.min_f1 is not None and (res["overall"]["f1"] or 0) < args.min_f1:
        raise SystemExit(f"F1 {res['overall']['f1']:.2f} is below threshold {args.min_f1}")


if __name__ == "__main__":
    main()
