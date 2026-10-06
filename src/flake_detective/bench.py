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
from flake_detective.types import Cause


def searched_causes(inv) -> set[str]:
    """The causes whose arm actually ran in this investigation.

    Arms map to causes through classify.ARM_CAUSE, which is where that correspondence is
    already defined; restating it here is how the two would drift apart. `nondeterminism`
    is not an arm - it is what the baseline alone establishes - so it is always searched.
    """
    from flake_detective.classify import ARM_CAUSE

    ran = {arm.name for arm in inv.arms} - {"baseline"}
    causes = {Cause.NONDETERMINISM.value}
    for arm in ran:
        cause = ARM_CAUSE.get(arm)
        if cause is not None:
            causes.add(cause.value)
    # The order arm establishes that a test depends on order. It cannot establish the
    # DIRECTION - "another test breaks it" versus "it needs another test" - and
    # classify.py is explicit that the isolation arm is what settles that, with the advice
    # saying so rather than guessing when it is unsettled. So needs-another-test is only
    # scoreable when isolation ran: with the default arms the tool correctly reports ORDER
    # with an undetermined direction, and counting that as a misattribution would penalise
    # it for refusing to guess.
    if "order" in ran:
        causes.add(Cause.ORDER.value)
    if "isolation" in ran:
        causes.add(Cause.ORDER.value)
        causes.add(Cause.NEEDS_TEST.value)
    return causes


def run(
    runs: int = 7,
    timeout: float = 120.0,
    progress=None,
    python: str = "",
    jobs: int = 1,
    arms: tuple[str, ...] | None = None,
) -> dict:
    """Investigate the fixture and score the result against its answer key.

    `jobs` is safe to raise here, unlike on an arbitrary suite: the fixture shares no
    file, port or database between processes, so concurrent runs cannot collide.

    `arms` defaults to the same three the tool itself defaults to, so the published
    numbers keep their meaning. Pass more to score the causes those arms establish: the
    fixture now has a known positive for every one of the seven arms, and without this
    parameter four of them could never be reached - `needs-other-test` in particular,
    whose direction only the isolation arm settles. Causes whose arm did not run are
    reported under `detail["unscoreable"]` and excluded from both rates.
    """
    say = progress or (lambda *_a, **_k: None)
    started = time.time()

    with tempfile.TemporaryDirectory(prefix="flake-bench-") as tmp:
        repo = fixture.write(Path(tmp) / "suite")
        say(f"fixture: {len(fixture.FILES)} files, {len(fixture.TRUTH)} tests, {runs} runs/arm")

        inv = investigate(
            repo,
            "",
            Options(
                runs=runs,
                timeout=timeout,
                python=python,
                jobs=jobs,
                order_seed=0,
                **({"arms": tuple(arms)} if arms else {}),
            ),
            progress=say,
        )

    if inv.interrupted:
        # A partial benchmark has no score, and a sweep must not carry on to the next
        # run count. The command line turns this into "interrupted" and exit 130.
        raise KeyboardInterrupt
    if not inv.ok:
        # A benchmark that could not run has no score. Reporting 0 of 4 detected would
        # read as a classifier that failed, when nothing was classified at all.
        return {
            "runs_per_arm": runs,
            "seconds": round(time.time() - started, 1),
            "error": inv.problem or "these arms scored no runs: " + ", ".join(inv.incomplete),
            "investigation": as_json(inv),
        }

    found = {f.test_id: f.cause.value for f in inv.flakes}
    # Which causes were actually looked for. An arm the platform cannot run - timezone and
    # locale on Windows, parallel without pytest-xdist in the target's environment - finds
    # nothing, and counting its known positive as a miss would make the detection rate a
    # property of the machine rather than of the classifier.
    scored = fixture.score(found, searched=searched_causes(inv))

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


def sweep(
    counts=(1, 2, 3, 5, 7, 11),
    timeout: float = 120.0,
    progress=None,
    python: str = "",
    jobs: int = 1,
) -> dict:
    """The same fixture at several run counts.

    A single accuracy number is close to meaningless without the run count beside it, and
    the curve says more than any point on it: it shows which findings are cheap and which
    have to be paid for. An arm that lands on a different failure rate than the baseline is
    visible at one run each; a test that flips *within* an arm needs enough runs to catch
    both sides of the flip.
    """
    say = progress or (lambda *_a, **_k: None)
    rows = []
    for n in counts:
        say(f"sweep: {n} runs per arm")
        r = run(runs=n, timeout=timeout, python=python, jobs=jobs, progress=say)
        if "error" in r:
            return {"sweep": rows, "error": r["error"]}
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


def _error_text(res: dict) -> str:
    return "\n".join(
        [
            "=" * 66,
            "BENCHMARK DID NOT RUN - there is no score",
            "=" * 66,
            "",
            *res["error"].splitlines(),
        ]
    )


def sweep_text(res: dict) -> str:
    if "error" in res:
        return _error_text(res)
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
    if "error" in res:
        return _error_text(res)
    d = res["detail"]
    out = [
        "=" * 66,
        "BENCHMARK - a suite whose flaky tests have known causes",
        "=" * 66,
        f"{res['runs_per_arm']} runs per arm, {res['seconds']}s",
        "",
        f"  detection        {d['detected']}/{d['flaky_in_fixture']}"
        f"   ({res['detection']:.0%})  flaky tests seen to flip",
        (
            f"  attribution      {d['correct_cause']}/{d['detected']}"
            f"   ({res['attribution']:.0%})  of those, right cause"
            if d["detected"]
            else "  attribution      -      nothing detected, so nothing to attribute"
        ),
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
