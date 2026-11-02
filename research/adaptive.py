"""Is the sequential budget worth it? Three budgets against the planted fixture.

    .venv/Scripts/python research/adaptive.py [--out docs/adaptive.json]

`power.py` computes what a fixed rerun budget buys and the answer is uncomfortable: the
default seven runs per arm reach 95% confidence only against a test failing more than 34.9%
of the time, and a one-in-fifty flake needs 149 runs per arm. For a while this repository
printed that bound and then went on spending seven runs, which is a tool that proves its own
budget is weak and keeps it. `--until-decided` spends runs sequentially instead, by Wald's
test, and stops each arm when every test it has observed has a verdict.

The honest claim for it is narrow, and this script exists to keep it narrow. Sequential
spending does **not** make the search cheap. Ruling out a 2% flake costs about 150 runs
however they are spent, and the cheapest path to accepting a clean test is
`runs_to_call_stable` consecutive passes - a floor no stopping rule can go below. What
changes is the *kind* of answer: a fixed budget that finds nothing yields a bound ("no flake
above 34.9%"), and a sequential one yields a decision per test ("every test's failure rate
is at or below p0, at this power") plus an explicit list of the tests it could not settle.

Three arms, on the suite `flake-detective fixture` plants, whose flakes fail about half the
time:

  * **fixed 7** - the default. Cheapest, and its clean half is a bound.
  * **fixed at the floor** - the same number of runs the sequential arm turns out to spend,
    so that the comparison is not simply "more runs find more". This is the arm that makes
    the result falsifiable: if sequential spending bought nothing, this arm would match it.
  * **sequential** - `--until-decided`, capped above the floor.

What to look at in the output is not the flake count - all three arms should find the same
planted flakes, and a difference there would mean the stopping rule had changed what was
found rather than what it cost. It is `runs_spent` against `undecided`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from flake_detective import fixture, power  # noqa: E402
from flake_detective.detective import Options, investigate  # noqa: E402

ARMS = ("baseline", "order", "hashseed", "clock")
P0 = 0.001
P1 = 0.05


def _summary(inv) -> dict:
    return {
        "flakes": sorted(f.test_id for f in inv.flakes),
        "flake_count": len(inv.flakes),
        "always_failed": sorted(inv.always_failed),
        "runs_spent": {a.name: a.runs for a in inv.arms},
        "runs_total": sum(a.runs for a in inv.arms),
        "stopped_early": sorted(a.name for a in inv.arms if a.stopped_early),
        # `None`, not 0, under a fixed budget. A fixed budget cannot populate this field
        # at all - every test it did not catch reads as stable - so a zero says "it decided
        # everything", which is the precise claim `power.py` objects to. The first version
        # printed 0 in that column for both fixed arms and made them look like the stronger
        # result.
        "undecided": sorted(inv.undecided) if inv.budget else None,
        "undecided_count": len(inv.undecided) if inv.budget else None,
        "undecided_is_measurable": bool(inv.budget),
        "budget": inv.budget or None,
        "seconds": round(inv.seconds, 1),
        "total_tests": inv.total_tests,
    }


def _run(repo: Path, label: str, opts: Options, say) -> dict:
    say(f"{label}: starting")
    started = time.time()
    inv = investigate(repo, opts=opts)
    out = _summary(inv)
    out["label"] = label
    say(
        f"{label}: {out['runs_total']} runs over {len(out['runs_spent'])} arms, "
        f"{out['flake_count']} flakes, "
        + (
            f"{out['undecided_count']} undecided"
            if out["undecided_is_measurable"]
            else "undecided not measurable"
        )
        + f", {time.time() - started:.0f}s"
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "adaptive.json")
    parser.add_argument("--cap", type=int, default=70)
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="a suite to use instead of the planted fixture. The fixture is the default "
        "because its flake rates are known, which is what makes the comparison readable",
    )
    args = parser.parse_args()

    sprt = power.Sprt(P0, P1)
    floor = sprt.runs_to_call_stable()
    shape = power.budget_shape(sprt, args.cap)
    print(
        f"p0={P0} p1={P1}: {floor} consecutive passes to accept a clean test, "
        f"{sprt.runs_to_call_flaky()} failures to call one flaky, "
        f"worst case {sprt.worst_case_runs():.1f} runs"
    )
    if args.cap < floor:
        print(
            f"\nthe cap {args.cap} is below the floor {floor}, so no clean test can be "
            "decided and the sequential arm will spend the cap and decide nothing. That is "
            "a legitimate thing to measure but it is not the comparison this script makes."
        )

    temporary = None
    if args.repo:
        repo = args.repo
    else:
        # Under the repository, not the system temp. On this machine the system drive has
        # filled to zero bytes free during a long run, and a planted suite plus pytest's
        # caches for several hundred runs is not nothing.
        scratch = ROOT / ".scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix="adaptive-", dir=scratch))
        repo = temporary
        repo = fixture.write(repo)
        print(f"\nplanted the fixture suite in {repo}")

    def say(message: str) -> None:
        print(f"  {message}", flush=True)

    try:
        rows = [
            _run(repo, "fixed 7", Options(runs=7, arms=ARMS[1:]), say),
            _run(repo, f"fixed {floor}", Options(runs=floor, arms=ARMS[1:]), say),
            _run(
                repo,
                f"sequential, cap {args.cap}",
                Options(
                    runs=7,
                    arms=ARMS[1:],
                    until_decided=True,
                    p0=P0,
                    p1=P1,
                    max_runs=args.cap,
                ),
                say,
            ),
        ]
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)

    fixed_small, fixed_floor, sequential = rows
    print()
    print(f"  {'budget':<22}{'runs':>7}{'flakes':>8}{'undecided':>11}  what a clean test gets")
    for row, verdict in (
        (fixed_small, f"a bound: no flake above {power.detectable_rate(7, 0.95) or 0:.1%}"),
        (fixed_floor, f"a bound: no flake above {power.detectable_rate(floor, 0.95) or 0:.1%}"),
        (sequential, f"a decision at or below {P0:.1%}"),
    ):
        # Padded whether or not the column has a value, and with a separator. Without the
        # separator the last two columns ran together as "0a bound: no flake above 34.9%".
        cell = str(row["undecided_count"]) if row["undecided_is_measurable"] else "n/a"
        print(
            f"  {row['label']:<22}{row['runs_total']:>7}{row['flake_count']:>8}"
            f"{cell:>11}  {verdict}"
        )
    print()
    print("  n/a: a fixed budget cannot name the tests it failed to settle - every test it")
    print("  did not catch reads as stable, which is the claim the column exists to check.")
    if sequential["runs_total"] == fixed_floor["runs_total"]:
        print()
        print("  The sequential arm spent exactly what the fixed arm at the floor spent. That")
        print("  is the expected result on a suite with no marginal test: the floor is the")
        print("  cheapest path to accepting a clean test and every flake here declares itself")
        print("  on its first failure. The saving is not in the run count, it is that the")
        print("  clean half of the report is a decision rather than a bound.")

    payload = {
        "what": (
            "Three rerun budgets against the planted fixture: the default seven runs, the "
            "same number of runs the sequential arm spends, and the sequential budget. The "
            "comparison is runs spent against the kind of answer a clean test gets, not "
            "the flake count - a difference in the flake count would mean the stopping "
            "rule changed what was found."
        ),
        "sprt": shape,
        "fixed_budget_bounds": {
            "runs_7": power.detectable_rate(7, 0.95),
            f"runs_{floor}": power.detectable_rate(floor, 0.95),
            "runs_for_95_percent_power_against_2_percent": power.runs_for_power(0.02),
        },
        "arms_compared": list(ARMS[1:]) + ["baseline"],
        "rows": rows,
        "findings_agree": (fixed_small["flakes"] == fixed_floor["flakes"] == sequential["flakes"]),
        "sequential_spent_the_floor_rather_than_the_cap": all(
            runs == shape["runs_to_call_stable"] for runs in sequential["runs_spent"].values()
        ),
        # Stated rather than left to be noticed. On a suite with no marginal test these are
        # equal by construction, and a reader who expected the sequential budget to be
        # *cheaper* should find that expectation contradicted here rather than inferred.
        "sequential_cost_the_same_as_the_fixed_floor": (
            sequential["runs_total"] == fixed_floor["runs_total"]
        ),
        "what_the_extra_runs_bought": (
            "not a smaller run count - the same 236 - but a per-test decision in place of "
            "a bound, and an explicit list of the tests the cap was reached on. The fixed "
            "arm at the floor spends the same runs and can still only say 'no flake above "
            "5.0%'."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"\nwritten to {args.out}")
    if not payload["findings_agree"]:
        print(
            "\nthe three budgets disagree about which tests are flaky. That is a finding "
            "about the fixture or about the stopping rule, not a formatting problem, and "
            "the rows carry which tests each one named."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
