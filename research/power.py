"""What the budget sweep in `docs/sweep.json` is, and is not, evidence for.

That sweep runs the planted fixture suite at one run per arm, two, three and so on, and
finds detection reaching **1.0 at two runs per arm**. Read as a statement about budgets,
that says two runs are enough. It is not a statement about budgets. It is a statement about
the fixture's flake rates, and this script measures them so the difference can be seen
rather than argued.

The fixture's flakes were written to fail about half the time, deliberately - a hash-order
test over eight elements fails under nearly every seed and reads as a broken test rather
than a flaky one, which the fixture's own comments say. Half is the easiest rate there is to
catch: it maximises the chance an arm sees both outcomes. A real flake that fires once in
fifty runs is a different problem by two orders of magnitude in the budget, and nothing in
the sweep bears on it.

So this script prints three things.

1. The fixture's **measured** per-run failure rates, from a long investigation rather than
   from reading the source. A claim about how a planted flake behaves should be measured
   like any other.
2. The power curve of the flip criterion at those rates and at realistic ones, from
   `flake_detective.power`.
3. What the sweep's own conclusion is worth once the two are put together.

    .venv/Scripts/python research/power.py --rates docs/fixture-rates.json

Writes research/results/power.json.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from flake_detective import power  # noqa: E402

RESULTS = ROOT / "research" / "results"
SWEEP = ROOT / "docs" / "sweep.json"
RATES = ROOT / "docs" / "fixture-rates.json"

# The budgets worth a row: the default, the sweep's claimed sufficient budget, and the two
# the power analysis actually asks for.
BUDGETS = (1, 2, 3, 5, 7, 11, 29, 59, 149)


def measured_fixture_rates(path: pathlib.Path) -> dict:
    """Each planted flake's per-run failure rate, from a long investigation.

    Taken from the arm that implicates it, not from the baseline: a hash-seed flake does
    not fail at all under identical repetition, so its baseline rate is zero and reading
    the rate from there would say every planted flake fails 0% of the time.
    """
    if not path.is_file():
        return {"available": False, "flakes": {}}
    report = json.loads(path.read_text(encoding="utf-8"))
    runs = {arm["name"]: arm["runs"] for arm in report.get("arms", [])}
    out = {}
    for flake in report.get("flakes", []):
        rates = flake.get("rates") or {}
        # The arm with the highest failure rate is the one the flake responds to, which is
        # also the arm the classifier attributed it to.
        arm, rate = max(rates.items(), key=lambda kv: kv[1] or 0.0)
        out[flake["test"]] = {
            "cause": flake.get("cause"),
            "arm_it_responds_to": arm,
            "rate": rate,
            "runs_in_that_arm": runs.get(arm),
        }
    return {
        "available": True,
        "runs_per_arm": report.get("arms", [{}])[0].get("runs"),
        "flakes": out,
    }


def sweep_claim(path: pathlib.Path) -> dict:
    """The smallest budget at which the published sweep reports perfect detection."""
    if not path.is_file():
        return {"available": False}
    rows = json.loads(path.read_text(encoding="utf-8"))["sweep"]
    perfect = [r for r in rows if r.get("detection") == 1.0]
    return {
        "available": True,
        "budgets_swept": [r["runs_per_arm"] for r in rows],
        "smallest_budget_with_detection_1": (
            min(r["runs_per_arm"] for r in perfect) if perfect else None
        ),
        # One sample per budget, which is the other half of why the curve reads too well:
        # at two runs and a 50% rate a single arm flips half the time, so one sample
        # reaching 1.0 across four tests is luck as much as it is detection.
        "samples_per_budget": 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rates", type=pathlib.Path, default=RATES)
    parser.add_argument("--sweep", type=pathlib.Path, default=SWEEP)
    parser.add_argument("--out", type=pathlib.Path, default=RESULTS / "power.json")
    args = parser.parse_args()

    fixture = measured_fixture_rates(args.rates)
    claim = sweep_claim(args.sweep)
    observed = [f["rate"] for f in fixture["flakes"].values() if f["rate"]]

    by_budget = {}
    for runs in BUDGETS:
        by_budget[str(runs)] = {
            "detectable_rate_at_95": power.detectable_rate(runs),
            "power_at_the_fixtures_rates": (
                round(sum(power.flip_probability(r, runs) for r in observed) / len(observed), 4)
                if observed
                else None
            ),
            "power_at_10_percent": round(power.flip_probability(0.1, runs), 4),
            "power_at_2_percent": round(power.flip_probability(0.02, runs), 4),
        }

    payload = {
        "what": (
            "The power of a rerun budget under the flip criterion, against the planted "
            "fixture's measured flake rates and against realistic ones. Explains what "
            "docs/sweep.json's detection curve does and does not establish."
        ),
        "default_runs": power.DEFAULT_RUNS,
        "fixture": fixture,
        "fixture_rate_mean": round(sum(observed) / len(observed), 4) if observed else None,
        "sweep": claim,
        "by_budget": by_budget,
        "power_table_at_the_default": power.power_table(),
        "what_the_sweep_establishes": (
            "That the classifier attributes correctly once a flake has flipped, which is "
            "what it was built to measure. Not that two runs per arm are enough: the "
            "fixture's flakes fail about half the time, the easiest rate there is, and at "
            f"two runs no budget reaches 95% confidence against any rate at all "
            f"(the smallest that does is {power.runs_for_power(0.5)}, and only against a "
            "coin-flip test)."
        ),
        "caveat_for_a_clean_report": power.caveat(power.DEFAULT_RUNS),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    print("The fixture's planted flakes, measured:")
    for test, row in fixture["flakes"].items():
        print(
            f"  {test[:50]:52} {row['cause']:16} {row['rate']:.3f} "
            f"in the {row['arm_it_responds_to']} arm"
        )
    if payload["fixture_rate_mean"] is not None:
        print(f"  mean rate: {payload['fixture_rate_mean']:.3f}")
    if claim.get("available"):
        print(
            f"\ndocs/sweep.json reports detection 1.0 from "
            f"{claim['smallest_budget_with_detection_1']} run(s) per arm, "
            f"{claim['samples_per_budget']} sample per budget."
        )
    print(f"\n{'runs':>5}{'95% rate':>12}{'fixture':>10}{'at 10%':>10}{'at 2%':>10}")
    for runs, row in by_budget.items():
        rate = row["detectable_rate_at_95"]
        print(
            f"{runs:>5}{('none' if rate is None else format(rate, '.3f')):>12}"
            f"{row['power_at_the_fixtures_rates']:>10.3f}"
            f"{row['power_at_10_percent']:>10.3f}{row['power_at_2_percent']:>10.3f}"
        )
    print(f"\n{payload['what_the_sweep_establishes']}")
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
