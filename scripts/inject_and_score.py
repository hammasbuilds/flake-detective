"""Score detection by planting flakes of a known cause in REAL suites.

    python scripts/inject_and_score.py <repo> [<repo> ...] [--runs N] [--json out.json]

Why this exists, and why the fixture suite is not enough on its own.

`fixture.py` builds a suite that is flaky in specified ways, and scores whether
the classifier names the right cause. That measures attribution, and it is worth
measuring. What it cannot do is establish that this tool finds flakes in code it
was not written alongside: the fixture's author and the classifier's author are
the same person, so a detector tuned to one is tuned to the other.

The obvious alternative is to mine real history for commits that fixed a flaky
test and revert them. That was tried first and it does not scale here. Searching
8,679 commits across flask and requests for `\\bflaky\\b|deflake|intermittent|
race condition|heisenbug` and keeping only commits that touch a test file yields
**one** usable case - flask 6d65595a3, "try to address flakiness of lazy loading
test". A first, looser pattern appeared to find dozens, all of which were
`pyflakes` and `flaskext` matching `flak`. Ground truth from history needs
dozens of large repositories, not two.

So: plant the flake instead, but plant it in somebody else's suite. The injected
test is written here, so its cause is known exactly. Everything around it -
hundreds of real tests, real fixtures, real imports, real conftest - is not, and
that surrounding noise is what the fixture cannot reproduce. A detector that
finds a planted order dependence among 800 real tests has done something the
fixture cannot demonstrate.

What this measures: **recall per cause, on real suites.** What it does not
measure: whether the causes chosen here are the ones that matter in the wild.
Only a real corpus answers that, and the paragraph above is why there isn't one.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from flake_detective.detective import Options, investigate  # noqa: E402
from flake_detective.types import Cause  # noqa: E402

# Each planted file is a pair: one test that makes the mess and one that trips on
# it, or a single test that reads something it should not. Deliberately small -
# the point is that the surrounding suite is real, not that the flake is clever.
#
# Every planted test name starts with `test_planted_` so scoring never has to
# guess which finding is the planted one.

ORDER = '''\
"""Planted: an order dependence. Cause: ORDER."""

_LEFTOVER: list[int] = []


def test_planted_order_writer():
    _LEFTOVER.append(1)
    assert _LEFTOVER


def test_planted_order_victim():
    # Passes when it runs first, fails after its neighbour. Nothing resets it.
    assert _LEFTOVER == []
'''

HASH_SEED = '''\
"""Planted: iteration order of a set of strings. Cause: HASH_SEED."""


def test_planted_hashseed_victim():
    # str hashing is salted per process, so the first element of this set is
    # whichever one PYTHONHASHSEED puts there.
    seen = {"alpha", "beta", "gamma", "delta", "epsilon"}
    assert next(iter(seen)) == "alpha"
'''

CLOCK = '''\
"""Planted: a wall-clock read. Cause: CLOCK."""

import datetime


def test_planted_clock_victim():
    # True on an even-numbered day and false on an odd one.
    assert datetime.date.today().day % 2 == 0
'''

NONDETERMINISM = '''\
"""Planted: unseeded randomness. Cause: NONDETERMINISM."""

import random


def test_planted_random_victim():
    assert random.random() < 0.5
'''

PLANTS = {
    Cause.ORDER: ORDER,
    Cause.HASH_SEED: HASH_SEED,
    Cause.CLOCK: CLOCK,
    Cause.NONDETERMINISM: NONDETERMINISM,
}


def test_dir(repo: Path) -> Path | None:
    """Where this project keeps its tests, or None if it has no obvious place.

    Planting into the wrong directory would measure nothing: the file has to be
    collected by the same pytest invocation as the real suite, under the same
    conftest, or the planted test is running alone in all but name.
    """
    for candidate in ("tests", "test"):
        path = repo / candidate
        if path.is_dir():
            return path
    if list(repo.glob("test_*.py")):
        return repo
    # A package that keeps its tests inside itself, as toolz does (toolz/tests).
    for candidate in sorted(repo.glob("*/tests")) + sorted(repo.glob("*/test")):
        if candidate.is_dir() and list(candidate.glob("test_*.py")):
            return candidate
    return None


def score_one(
    repo: Path, cause: Cause, body: str, runs: int, python: str, jobs: int = 1, seed: int = 0
) -> dict:
    """Plant one flake in a copy of the repo and report what came back."""
    work = Path(tempfile.mkdtemp(prefix="inject_"))
    try:
        copy = work / repo.name
        # Copy rather than plant in place. Planting into the real repository and
        # deleting afterwards works right up to the run that crashes.
        shutil.copytree(
            repo,
            copy,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                "__pycache__",
                ".pytest_cache",
                "node_modules",
                ".mypy_cache",
                ".ruff_cache",
            ),
        )
        target = test_dir(copy)
        if target is None:
            return {"repo": repo.name, "cause": cause.value, "outcome": "no test directory"}
        (target / "test_planted_flake.py").write_text(body, encoding="utf-8")

        started = time.time()
        inv = investigate(copy, None, Options(runs=runs, python=python, jobs=jobs, order_seed=seed))
        planted = [f for f in inv.flakes if "planted" in f.test_id]
        other = [f for f in inv.flakes if "planted" not in f.test_id]

        # Collected is not the same as ran. devign-leakage collects 20 tests here
        # and scores zero runs in every arm, because its suite needs packages this
        # interpreter does not have - and flake-detective correctly reports zero
        # runs rather than "nothing flaky". Counting that as a MISS would blame the
        # detector for an environment problem and understate its recall. Pass
        # --python pointing at the target's own interpreter to score these.
        ran = min((a.runs for a in inv.arms), default=0)
        if not inv.total_tests:
            outcome = "nothing collected"
        elif ran == 0:
            outcome = "arms scored zero runs (suite needs its own environment)"
        else:
            outcome = "ok"
        return {
            "repo": repo.name,
            "cause": cause.value,
            "total_tests": inv.total_tests,
            "min_arm_runs": ran,
            "found": bool(planted),
            "reported_cause": planted[0].cause.value if planted else None,
            "cause_correct": bool(planted) and planted[0].cause is cause,
            "collateral": [f.test_id for f in other],
            "seconds": round(time.time() - started, 1),
            "outcome": outcome,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repos", nargs="+", type=Path)
    # 7 is the tool's own default; the dated 48-plant study predates it and used 5.
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0, help="order-arm shuffle seed")
    ap.add_argument("--python", default="")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    rows = []
    for repo in args.repos:
        if not repo.is_dir():
            print(f"  {repo}: not a directory")
            continue
        for cause, body in PLANTS.items():
            row = score_one(repo, cause, body, args.runs, args.python, args.jobs, args.seed)
            rows.append(row)
            if row.get("outcome") != "ok":
                mark = "skip"
            elif row.get("cause_correct"):
                mark = "."
            elif row.get("found"):
                mark = "?"
            else:
                mark = "MISS"
            print(
                f"  {row['repo']:<28} {cause.value:<15} {mark:<5}"
                f" reported={row.get('reported_cause')}"
                f" tests={row.get('total_tests')}"
                f" collateral={len(row.get('collateral') or [])}"
                f"  {row.get('outcome')}",
                flush=True,
            )

    scored = [r for r in rows if r.get("outcome") == "ok"]
    found = [r for r in scored if r["found"]]
    right = [r for r in scored if r["cause_correct"]]
    collateral = sum(len(r["collateral"]) for r in scored)
    print()
    print(f"{len(scored)} scoreable plants across {len({r['repo'] for r in scored})} repositories")
    if scored:
        print(
            f"  detected              : {len(found)}/{len(scored)} = {len(found) / len(scored):.0%}"
        )
        print(
            f"  cause named correctly : {len(right)}/{len(scored)} = {len(right) / len(scored):.0%}"
        )
    print(f"  findings that were NOT the planted test: {collateral}")
    print("  (those are either real flakes in the target suite or false positives;")
    print("   this harness cannot tell which, and does not claim to)")

    per_cause: dict[str, list[dict]] = {}
    for r in scored:
        per_cause.setdefault(r["cause"], []).append(r)
    if per_cause:
        print("\n  per cause:")
        for cause, group in per_cause.items():
            ok = sum(1 for r in group if r["cause_correct"])
            print(f"    {cause:<16} {ok}/{len(group)}")

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
