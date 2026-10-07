"""
Verification-agent evaluation: does the agent reach the right verdict on
rewrites whose correctness we already know?

The golden set is six pairs, three of which preserve the original's results
and three of which change them in ways that only show up on specific data --
including the UNION ALL bug this project actually shipped (commit 71c7907) and
the NOT IN / NOT EXISTS trap that appears whenever the subquery column is
nullable.

What this eval is really measuring, and it is worth being precise because the
headline number alone is misleading: the verifier has a deterministic oracle
(execute both queries, compare the results as multisets) and an LLM loop that
runs only when the oracle finds a match. A mismatch is a *proof* of
non-equivalence, so every wrong pair should be caught with no LLM involved at
all, provided the fixture contains the distinguishing rows. That is a claim
about fixture design, not about model quality, and `decided without LLM` below
is the number that tests it.

The LLM therefore only ever sees the correct pairs, where the risk it is being
measured on is the opposite one: a false alarm. Reporting accuracy without
splitting those two populations would let a good fixture take credit for the
model, or a talkative model get blamed for the fixture.

Usage (from backend/):
    python -m evals.eval_verifier                  # report + JSON to evals/results/
    python -m evals.eval_verifier --no-llm         # oracle only, no network
    python -m evals.eval_verifier --min-accuracy 1.0   # exit 1 below that (for CI)
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from app import verify_fixture
from app.config import config
from app.verifier import Verdict, verify_rewrite

EVAL_DIR = Path(__file__).parent
GOLDEN_PATH = EVAL_DIR / "golden_verifier.json"
RESULTS_DIR = EVAL_DIR / "results"


def run(golden: Path, use_llm: bool = True, db_path: Path | None = None) -> dict:
    pairs = json.loads(golden.read_text(encoding="utf-8"))["pairs"]

    # Rebuild rather than reuse: the whole eval is meaningless against a stale
    # fixture, since every wrong pair depends on specific rows being present.
    fixture = verify_fixture.build(db_path or config.VERIFY_DB_PATH)

    # --no-llm blanks the key, which the verifier treats as a deliberate skip.
    # It exercises the oracle alone -- the half that must work offline.
    original_key = config.GROQ_API_KEY
    if not use_llm:
        config.GROQ_API_KEY = ""

    results = []
    try:
        for pair in pairs:
            res = verify_rewrite(pair["original"], pair["rewrite"], db_path=fixture)
            expected = Verdict(pair["expected_verdict"])
            results.append({
                "id": pair["id"],
                "category": pair["category"],
                "expected": expected.value,
                "actual": res.verdict.value,
                "correct": res.verdict is expected,
                "steps_taken": res.steps_taken,
                "decided_without_llm": res.decided_without_llm,
                "probe_status": res.probe_status,
                "probe_error": res.probe_error,
                "evidence": res.evidence,
                "tool_calls": [c.model_dump(mode="json") for c in res.tool_calls],
                "why": pair.get("why", ""),
                "exposed_by": pair.get("exposed_by", ""),
            })
    finally:
        config.GROQ_API_KEY = original_key

    correct = [r for r in results if r["correct"]]
    by_category: dict[str, dict] = {}
    for r in results:
        c = by_category.setdefault(r["category"], {"total": 0, "correct": 0})
        c["total"] += 1
        c["correct"] += int(r["correct"])

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "golden_set": golden.name,
        "llm_enabled": use_llm,
        "model": config.LLM_MODEL if use_llm else None,
        "max_steps": config.VERIFY_MAX_STEPS,
        "n_pairs": len(results),
        "n_correct": len(correct),
        "accuracy": len(correct) / len(results) if results else 0.0,
        "decided_without_llm": sum(r["decided_without_llm"] for r in results),
        # Counted separately from accuracy on purpose. A failed probe still
        # yields the right verdict -- the deterministic match stands on its own
        # -- so accuracy alone stays at 100% while the Groq integration is
        # broken. That is precisely what happened on the first live run, and
        # this count is what makes it visible.
        "probe_failures": sum(r["probe_status"] == "failed" for r in results),
        "per_category": {
            k: {**v, "accuracy": v["correct"] / v["total"]}
            for k, v in sorted(by_category.items())
        },
        "pairs": results,
    }


def print_report(res: dict) -> None:
    print(f"\nVerifier eval on {res['n_pairs']} rewrite pairs"
          f"  (LLM {'on, ' + str(res['model']) if res['llm_enabled'] else 'off'})")
    print(f"Verdicts correct   {res['n_correct']}/{res['n_pairs']}  "
          f"({res['accuracy']:.0%})")
    print(f"Decided without the LLM   {res['decided_without_llm']}/{res['n_pairs']}"
          "   (deterministic comparison alone; these are proofs)")
    probed = res["n_pairs"] - res["decided_without_llm"]
    status = "none" if not res["probe_failures"] else f"{res['probe_failures']} FAILED"
    print(f"LLM probing runs   {probed}   failures: {status}\n")

    print(f"{'Category':<16}{'Correct':>10}")
    for name, c in res["per_category"].items():
        print(f"{name:<16}{c['correct']:>5}/{c['total']:<4} ({c['accuracy']:.0%})")

    print(f"\n{'Pair':<34}{'Expected':<26}{'Actual':<26}{'Steps':>6}{'LLM?':>6}")
    for r in res["pairs"]:
        mark = " " if r["correct"] else "X"
        print(f"{mark}{r['id']:<33}{r['expected']:<26}{r['actual']:<26}"
              f"{r['steps_taken']:>6}{('no' if r['decided_without_llm'] else 'yes'):>6}")

    print("\nEvidence returned per pair:")
    for r in res["pairs"]:
        print(f"  [{r['id']}]")
        print(f"      {r['evidence'][:300]}")

    broken = [r for r in res["pairs"] if r["probe_status"] == "failed"]
    if broken:
        print("\nLLM probing failures (the verdict may still be correct, but it "
              "was NOT reached by the agent):")
        for r in broken:
            print(f"  {r['id']}: {r['probe_error'][:220]}")

    wrong = [r for r in res["pairs"] if not r["correct"]]
    if wrong:
        print("\nWrong verdicts:")
        for r in wrong:
            print(f"  {r['id']}: expected {r['expected']}, got {r['actual']}")
            print(f"      why the pair is what it is: {r['why'][:200]}")
            print(f"      tool calls: "
                  f"{', '.join(c['tool'] for c in r['tool_calls']) or 'none'}")


def _make_stdout_printable() -> None:
    """Stop the report crashing on a character the console cannot encode.

    Unlike the other evals, this one prints LLM-generated text, which is not
    ASCII-bounded. A real run died here with UnicodeEncodeError because the
    model wrote a non-breaking hyphen (U+2011) and a Windows console defaults
    to cp1252. Losing a whole eval report -- and its exit code -- to a dash is
    absurd, so the stream degrades instead: UTF-8 where available, and
    replacement characters rather than an exception where not.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def main() -> None:
    _make_stdout_printable()
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-accuracy", type=float, default=None,
                        help="Fail (exit 1) if verdict accuracy is below this")
    parser.add_argument("--no-llm", action="store_true",
                        help="Run the deterministic oracle only, with no network calls")
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    args = parser.parse_args()

    if not args.golden.is_file():
        raise SystemExit(f"Golden set not found: {args.golden}")

    res = run(args.golden, use_llm=not args.no_llm)
    print_report(res)

    RESULTS_DIR.mkdir(exist_ok=True)
    suffix = "_no_llm" if args.no_llm else ""
    out = RESULTS_DIR / f"verifier_latest{suffix}.json"
    out.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"\nSaved {out.relative_to(EVAL_DIR.parent)}")

    if args.min_accuracy is not None and res["accuracy"] < args.min_accuracy:
        raise SystemExit(
            f"Accuracy {res['accuracy']:.2f} is below threshold {args.min_accuracy}"
        )


if __name__ == "__main__":
    main()
