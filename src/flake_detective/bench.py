"""Score the classifier against a suite whose causes are known.

Two numbers matter and they pull against each other:

    detection      of the flaky tests, how many were reported at all
    attribution    of those, how many got the *right* cause

and a third that keeps the other two honest:

    false positives   stable tests reported as flaky

A tool that reports every test as `nondeterminism` scores 100% detection. One that reports
nothing scores zero false positives. Only all three together say anything.

Detection has a hard ceiling that is not the classifier's fault. A test that fails one run in
five has a 1 - (4/5)^n chance of being *seen* to flip in n runs - at 5 runs, 67%. The
benchmark reports the run count beside the score, because the same classifier scores
differently at 3 runs and at 15, and quoting the number without it would be meaningless.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from flake_detective import fixture
from flake_detective.detective import Options, investigate
from flake_detective.report import as_json


def run(runs: int = 7, timeout: float = 120.0, progress=None) -> dict:
    say = progress or (lambda *_: None)
    started = time.time()

    with tempfile.TemporaryDirectory(prefix="flake-bench-") as tmp:
        repo = fixture.write(Path(tmp) / "suite")
        say(f"fixture: {len(fixture.FILES)} files, {len(fixture.TRUTH)} tests, {runs} runs/arm")

        inv = investigate(repo, "", Options(runs=runs, timeout=timeout), progress=say)

    found = {f.test_id: f.cause.value for f in inv.flakes}
    scored = fixture.score(found)

    n_flaky = scored["flaky_in_fixture"]
    n_stable = scored["stable_in_fixture"]
    return {
        "runs_per_arm": runs,
        "seconds": round(time.time() - started, 1),
        "detection": round(scored["detected"] / n_flaky, 3) if n_flaky else 0.0,
        "attribution": (
            round(scored["correct_cause"] / scored["detected"], 3) if scored["detected"] else 0.0
        ),
        "false_positive_rate": (
            round(len(scored["false_positives"]) / n_stable, 3) if n_stable else 0.0
        ),
        "detail": scored,
        "reported": found,
        "investigation": as_json(inv),
    }


def sweep(counts=(1, 2, 3, 5, 7, 11), timeout: float = 120.0, progress=None) -> dict:
    """The same fixture at several run counts.

    A single accuracy number is close to meaningless without the run count beside it, and
    the curve says more than any point on it: it shows which findings are cheap and which
    have to be paid for. An arm that lands on a different failure rate than the baseline is
    visible at one run each; a test that flips *within* an arm needs enough runs to catch
    both sides of the flip.
    """
    say = progress or (lambda *_: None)
    rows = []
    for n in counts:
        say(f"sweep: {n} runs per arm")
        r = run(runs=n, timeout=timeout)
        rows.append(
            {
                "runs_per_arm": n,
                "detection": r["detection"],
                "attribution": r["attribution"],
                "false_positive_rate": r["false_positive_rate"],
                "seconds": r["seconds"],
                "reported": r["reported"],
            }
        )
    return {"sweep": rows}


def sweep_text(res: dict) -> str:
    out = [
        "=" * 66,
        "RUNS PER ARM vs WHAT IS FOUND",
        "=" * 66,
        f"{'runs':>5}{'detection':>12}{'attribution':>14}{'false pos':>12}{'secs':>8}",
        "-" * 66,
    ]
    for r in res["sweep"]:
        out.append(
            f"{r['runs_per_arm']:>5}"
            f"{r['detection']:>11.0%}"
            f"{r['attribution']:>14.0%}"
            f"{r['false_positive_rate']:>12.0%}"
            f"{r['seconds']:>8.0f}"
        )
    return "\n".join(out)


def text(res: dict) -> str:
    d = res["detail"]
    out = [
        "=" * 66,
        "BENCHMARK - a suite whose flaky tests have known causes",
        "=" * 66,
        f"{res['runs_per_arm']} runs per arm, {res['seconds']}s",
        "",
        f"  detection        {d['detected']}/{d['flaky_in_fixture']}"
        f"   ({res['detection']:.0%})  flaky tests seen to flip",
        f"  attribution      {d['correct_cause']}/{d['detected'] or 1}"
        f"   ({res['attribution']:.0%})  of those, right cause",
        f"  false positives  {len(d['false_positives'])}/{d['stable_in_fixture']}"
        f"   ({res['false_positive_rate']:.0%})  stable tests wrongly flagged",
        "",
    ]
    if d["missed"]:
        out.append("missed (never seen to flip in this many runs):")
        out += [f"  {t}" for t in d["missed"]]
        out.append("")
    if d["misattributed"]:
        out.append("misattributed (flagged, wrong cause):")
        out += [f"  {t}  -> {c}" for t, c in d["misattributed"].items()]
        out.append("")
    if d["false_positives"]:
        out.append("false positives (stable tests reported as flaky):")
        out += [f"  {t}" for t in d["false_positives"]]
        out.append("")
    out.append("reported causes:")
    for t, c in sorted(res["reported"].items()):
        out.append(f"  {c:<16} {t}")
    return "\n".join(out)


def write_json(res: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=2), encoding="utf-8")
