"""Run every arm, then classify.

The baseline runs **first**, and not only for tidiness. If a suite turns out to be
thoroughly nondeterministic - a third of it flipping with nothing changed - the other arms
have nothing to add, and the honest report is "this suite is not stable enough to attribute
anything". Running them anyway would produce a confident cause for every one of those tests,
because a test that flips under identical conditions also flips when the order changes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from flake_detective import freeze
from flake_detective import run as arms_mod
from flake_detective.classify import classify
from flake_detective.types import Arm, Investigation

ARMS = ("order", "hashseed", "clock")


@dataclass
class Options:
    runs: int = 5
    timeout: float = 900.0
    arms: tuple[str, ...] = ARMS
    order_seed: int = 0
    python: str = ""
    """The interpreter to run the target suite with. Defaults to this one.

    Usually wrong to leave at the default on a real repository: the suite has to import the
    project and its dependencies, which live in that project's environment, not in
    flake-detective's. A suite that cannot import raises a collection error, which is scored
    as "unscoreable" rather than as failures - so the arms come back with zero runs and the
    report says so, instead of announcing that nothing is flaky.
    """

    freeze_clock: bool = True
    """Pin the wall clock in every arm except the clock arm, which varies it.

    Off is an escape hatch, not an option worth taking. A suite that will not run under a
    frozen clock - one that waits on a wall-clock deadline, say - can still be investigated
    for order and hash dependence, but its clock arm becomes meaningless and every
    date-dependent test in it will be reported as nondeterminism instead.
    """


def investigate(
    repo: Path,
    target: str = "",
    opts: Options | None = None,
    progress=None,
) -> Investigation:
    opts = opts or Options()
    say = progress or (lambda *_: None)
    started = time.time()
    epoch = freeze.BASELINE_EPOCH if opts.freeze_clock else None

    tests = arms_mod.collect(repo, target, python=opts.python)
    if not tests:
        # No tests collected is not "no flakes found". Say which it is.
        inv = Investigation(arms=[], total_tests=0)
        inv.seconds = time.time() - started
        return inv

    say(f"collected {len(tests)} tests")

    say(f"baseline: {opts.runs} identical runs")
    baseline = arms_mod.baseline_arm(repo, target, opts.runs, opts.timeout, epoch, opts.python)
    built: list[Arm] = [baseline]

    if "order" in opts.arms:
        say(f"order: {opts.runs} shuffled runs")
        built.append(
            arms_mod.order_arm(
                repo,
                target,
                tests,
                opts.runs,
                opts.timeout,
                epoch,
                opts.order_seed,
                opts.python,
            )
        )
    if "hashseed" in opts.arms:
        say(f"hashseed: {opts.runs} runs, PYTHONHASHSEED 1..{opts.runs}")
        built.append(
            arms_mod.hashseed_arm(repo, target, opts.runs, opts.timeout, epoch, opts.python)
        )
    if "clock" in opts.arms:
        if not opts.freeze_clock:
            # Without freezing there is nothing to vary: the clock already varies in every
            # arm, so this one would be a second baseline wearing a different label.
            say("clock: skipped (the clock is not frozen, so it cannot be varied)")
        else:
            say(f"clock: {opts.runs} runs, frozen at a different date each time")
            built.append(arms_mod.clock_arm(repo, target, opts.runs, opts.timeout, opts.python))

    inv = classify(built, tests)
    inv.seconds = time.time() - started
    return inv
