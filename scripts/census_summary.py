"""Reduce docs/third-party-census.json to the figures the README quotes.

The census plants four known flakes in each of a thousand real PyPI suites and records,
per arm, whether the detector found the plant and whether it named the right cause.
Nothing read the result: no README figure came from it and no test opened it, so a
1,000-project run finished and published nothing.

What it publishes, per cause:

  arms        packages where this cause was planted
  measured    ... whose arm actually ran the suite
  found       ... where the planted flake was detected
  cause_right ... where the reported cause was the planted one

The denominator is `measured`, never `arms`. Eight packages in one run scored zero runs
in all four arms - their suites need their own interpreter, which the scorer records and
nothing acted on - and their four misses each went into a per-cause rate: order read
94.9% where its measured arms give 99.4%.

Exclusion reasons are normalised. The clean-time gate writes the measured time into its
reason ("clean suite takes 31s, over the 25s cap"), so a thousand rows produced twenty
distinct strings for one gate and the table could not be read or summed. The raw string
stays in the row; the group is what is published.

    python scripts/census_summary.py [--census docs/third-party-census.json]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from inject_and_score import arm_measured  # noqa: E402

ROOT = HERE.parent

# One entry per gate, not per wording. Each pattern is anchored on the mechanism so a
# reason that carries a measured number in it cannot split into a row of its own.
REASON_GROUPS = (
    (re.compile(r"over the \d+s cap"), "clean suite slower than the cap"),
    (re.compile(r"^no arm ran the suite"), "no arm ran the suite (needs its own interpreter)"),
    (re.compile(r"^sdist ships no test directory"), "sdist ships no test directory"),
    (re.compile(r"^no test_.*under tests/"), "sdist ships no test_*.py"),
    (re.compile(r"collects no tests"), "clean suite collects no tests"),
    (re.compile(r"mostly broken"), "clean suite mostly broken"),
    (re.compile(r"^no sdist published"), "no sdist published"),
    (re.compile(r"^install "), "install failed"),
    (re.compile(r"^scorer produced no report"), "scorer produced no report"),
    (re.compile(r"^budget exceeded"), "budget exceeded"),
    (re.compile(r"^candidate failed"), "candidate failed"),
)


def reason_group(text: str) -> str:
    for pattern, name in REASON_GROUPS:
        if pattern.search(text):
            return name
    return (text or "no reason recorded")[:60]


def summarise(rows: list[dict]) -> dict:
    exclusions: collections.Counter = collections.Counter()
    by_cause: dict[str, dict] = {}
    measured_rows = 0
    collateral_arms = collateral_tests = 0
    # Collateral per package, not only per arm. A test named in EVERY arm of a package is
    # flaky independently of what was planted there, which is a different claim from "the
    # report sometimes names extra tests" - and the totals are not usable on their own:
    # two packages account for most of them.
    wrong_causes: collections.Counter = collections.Counter()
    collateral_sizes: list[int] = []
    packages_with_collateral = 0
    packages_collateral_in_every_arm = 0
    biggest: list[tuple[int, str]] = []

    for row in rows:
        if not isinstance(row, dict):
            exclusions["row is not a record"] += 1
            continue
        arms = row.get("score") or row.get("empty_score") or []
        # Re-applied here as well as at write time: the census takes --resume, so rows
        # written before the predicate existed keep whatever classification was current
        # the day they were measured.
        if not any(arm_measured(a) for a in arms):
            reasons = sorted({a.get("outcome") or "?" for a in arms})
            text = row.get("excluded") or (
                "no arm ran the suite: " + "; ".join(reasons) if arms else ""
            )
            exclusions[reason_group(text)] += 1
            continue
        measured_rows += 1
        ran = [a for a in arms if arm_measured(a)]
        sets = [set(a.get("collateral") or []) for a in ran]
        if any(sets):
            packages_with_collateral += 1
            biggest.append((max(len(x) for x in sets), str(row.get("package"))))
            collateral_sizes.extend(len(x) for x in sets if x)
            if all(sets) and set.intersection(*sets):
                packages_collateral_in_every_arm += 1
        for arm in arms:
            c = by_cause.setdefault(
                arm["cause"],
                {
                    "arms": 0,
                    "measured": 0,
                    "found": 0,
                    "cause_right": 0,
                    "unmeasured": 0,
                    "not_detected": 0,
                    "cause_unknown": 0,
                    "cause_wrong": 0,
                },
            )
            c["arms"] += 1
            if not arm_measured(arm):
                c["unmeasured"] += 1
                continue
            c["measured"] += 1
            c["found"] += bool(arm.get("found"))
            c["cause_right"] += bool(arm.get("cause_correct"))
            # The three ways an arm can fall short, kept apart. `cause_right` over
            # `measured` mixes "not detected" with "detected and misnamed", and those
            # are different claims about the tool: a README sentence written from the
            # difference 667 - 651 called all 16 of them wrong causes, when 2 were
            # plants never seen at all, 13 were reported as `unknown` - seen to flip,
            # cause declined - and exactly one was a wrong cause.
            if not arm.get("found"):
                c["not_detected"] += 1
            elif not arm.get("cause_correct"):
                if (arm.get("reported_cause") or "unknown") == "unknown":
                    c["cause_unknown"] += 1
                else:
                    c["cause_wrong"] += 1
                    wrong_causes[(arm["cause"], arm.get("reported_cause"))] += 1
            collateral_arms += bool(arm.get("collateral"))
            collateral_tests += len(arm.get("collateral") or [])

    causes = {}
    for name, c in sorted(by_cause.items()):
        causes[name] = {
            "arms": c["arms"],
            "measured": c["measured"],
            "unmeasured": c["unmeasured"],
            "found": c["found"],
            "cause_right": c["cause_right"],
            "not_detected": c["not_detected"],
            "cause_unknown": c["cause_unknown"],
            "cause_wrong": c["cause_wrong"],
            # None, not 0.0, when nothing was measured: a cause with no measured arm has
            # no rate, and 0% would read as "this cause is never detected".
            "found_share": round(c["found"] / c["measured"], 4) if c["measured"] else None,
            "cause_right_share": (
                round(c["cause_right"] / c["measured"], 4) if c["measured"] else None
            ),
        }

    total_measured = sum(c["measured"] for c in causes.values())
    return {
        "considered": len(rows),
        "scored": measured_rows,
        "excluded": len(rows) - measured_rows,
        "exclusions": dict(exclusions.most_common()),
        "causes": causes,
        "measured_arms": total_measured,
        "found": sum(c["found"] for c in causes.values()),
        "cause_right": sum(c["cause_right"] for c in causes.values()),
        "not_detected": sum(c["not_detected"] for c in causes.values()),
        "cause_unknown": sum(c["cause_unknown"] for c in causes.values()),
        "cause_wrong": sum(c["cause_wrong"] for c in causes.values()),
        "wrong_causes": [
            {"planted": a, "reported": b, "arms": n} for (a, b), n in wrong_causes.most_common()
        ],
        # How often a detected plant comes with other tests named alongside it. The
        # census records them per arm and no figure used them; a user who acts on the
        # report sees these names too.
        "arms_with_collateral": collateral_arms,
        "collateral_tests": collateral_tests,
        "packages_with_collateral": packages_with_collateral,
        # Of those, the ones where at least one test is named in every arm that ran:
        # flaky whatever was planted, so not an artefact of the plant.
        "packages_collateral_in_every_arm": packages_collateral_in_every_arm,
        # Median, because the mean is not usable here: the two largest packages carry
        # most of collateral_tests between them, and a per-arm average would describe
        # neither them nor the median suite.
        "collateral_per_affected_arm_median": (
            statistics.median(collateral_sizes) if collateral_sizes else None
        ),
        "largest_collateral_packages": [
            {"package": name, "arm_max": n} for n, name in sorted(biggest, reverse=True)[:3]
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--census", type=Path, default=ROOT / "docs" / "third-party-census.json")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "census-summary.json")
    args = ap.parse_args()
    if not args.census.exists():
        raise SystemExit(f"no census at {args.census} - run scripts/third_party_census.py first")
    summary = summarise(json.loads(args.census.read_text(encoding="utf-8")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    print(f"\nwritten to {args.out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
