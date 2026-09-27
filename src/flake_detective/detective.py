"""Run every arm, then classify.

The baseline runs **first**, and not only for tidiness. If a suite turns out to be
thoroughly nondeterministic - a third of it flipping with nothing changed - the other arms
have nothing to add, and the honest report is "this suite is not stable enough to attribute
anything". Running them anyway would produce a confident cause for every one of those tests,
because a test that flips under identical conditions also flips when the order changes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from flake_detective import freeze
from flake_detective import run as arms_mod
from flake_detective.classify import classify
from flake_detective.types import Arm, Investigation

ARMS = ("order", "hashseed", "clock")

# Available but not on by default. Each costs a full set of runs, and the three
# above catch the causes that turn up most; these three catch the ones that turn up
# worst. Opt in with --arms order,hashseed,clock,timezone,locale,parallel.
EXTRA_ARMS = ("timezone", "locale", "parallel", "isolation")
ALL_ARMS = ARMS + EXTRA_ARMS


@dataclass
class Options:
    runs: int = 7
    """Repetitions per arm. Seven, not five, and the difference was measured.

    The order arm exposes a two-test dependence only in the shuffles that put the
    culprit before the victim - about half of them - so n shuffles miss it with
    probability 2^-n. At five that is 3.1%, and it is not theoretical: planting an
    order dependence into model-serving-platform and suite-auditor and running at
    --runs 5 missed it in both, then found it at --runs 11 (6 failures of 11) and
    classified it correctly. Seven takes the bound to 0.8% for 40% more time.

    Raise it further on a suite worth being sure about. The report prints the
    bound this setting implies rather than leaving it to be worked out.
    """

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

    localise: bool = False
    """After classifying, bisect each order dependence to name the culprit.

    Off by default because it costs runs: about log2(n) pytest invocations per
    order-dependent test, plus two checks. On a suite where the whole run takes a
    minute that is cheap; on one that takes twenty it is not, and the choice
    belongs to whoever is waiting.
    """

    jobs: int = 1
    """How many pytest processes to run at once. One by default, and for a reason.

    Runs are independent processes, so several at once is faster in proportion to the
    cores free. But two copies of a suite running side by side collide on anything the
    suite fixes in place - a file in the repo, a port, a database name - and those
    collisions are exactly the flakes this tool looks for. Raise it only for a suite
    that is known not to share such things across processes.
    """

    freeze_clock: bool = True
    """Pin the wall clock in every arm except the clock arm, which varies it.

    Off is an escape hatch, not an option worth taking. A suite that will not run under a
    frozen clock - one that waits on a wall-clock deadline, say - can still be investigated
    for order and hash dependence, but its clock arm becomes meaningless and every
    date-dependent test in it will be reported as nondeterminism instead.
    """


def _duration(seconds: float) -> str:
    seconds = max(int(round(seconds)), 0)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


class _Progress:
    """Per-run progress with an estimate of what is left.

    Every run is a full pytest process, so a suite that takes twenty seconds costs
    twenty seconds per run, times the runs, times the arms. Without a count and an
    estimate, a long investigation looks exactly like a hung one.
    """

    def __init__(self, say, total: int) -> None:
        self.say = say
        self.total = max(total, 1)
        self.done = 0
        self.started = time.time()
        self.name = ""
        self.arm_done = 0
        self.arm_total = 0

    def start(self, name: str, units: int, what: str) -> None:
        self.name, self.arm_done, self.arm_total = name, 0, units
        self.say(f"{name}: {what}")

    def tick(self) -> None:
        self.done += 1
        self.arm_done += 1
        elapsed = time.time() - self.started
        left = elapsed / self.done * (self.total - self.done)
        eta = f", about {_duration(left)} left" if self.done < self.total else ""
        self.say(
            f"{self.name} {self.arm_done}/{self.arm_total}"
            f"   (run {self.done} of {self.total}, {_duration(elapsed)} so far{eta})",
            transient=True,
        )


def investigate(
    repo: Path,
    target: str = "",
    opts: Options | None = None,
    progress=None,
) -> Investigation:
    """Run every arm, then classify.

    `progress`, if given, is called as progress(message) for each stage and as
    progress(message, transient=True) after every pytest run.
    """
    opts = opts or Options()
    say = progress or (lambda *_a, **_k: None)
    started = time.time()
    epoch = freeze.BASELINE_EPOCH if opts.freeze_clock else None

    def failed(problem: str, total: int = 0) -> Investigation:
        inv = Investigation(arms=[], total_tests=total, problem=problem)
        inv.seconds = time.time() - started
        return inv

    missing = arms_mod.pytest_problem(opts.python, repo)
    if missing:
        return failed(missing)

    collection = arms_mod.collect_detailed(repo, target, python=opts.python)
    if collection.error:
        # No tests collected is not "no flakes found", and neither is a suite that
        # would not import. Say which it is, in pytest's own words.
        return failed(collection.error, len(collection.tests))
    tests = collection.tests
    say(f"collected {len(tests)} tests")

    # Decide every arm before running any, so the progress line can count down to
    # the end of the whole investigation rather than the end of one arm.
    n, t, py, jobs = opts.runs, opts.timeout, opts.python, opts.jobs
    plan: list[tuple[str, int, str, Callable[[Callable[[], None]], Arm]]] = [
        (
            "baseline",
            n,
            f"{n} identical runs",
            lambda tick: arms_mod.baseline_arm(repo, target, n, t, epoch, py, jobs, tick),
        )
    ]
    if "order" in opts.arms:
        plan.append(
            (
                "order",
                n,
                f"{n} shuffled runs",
                lambda tick: arms_mod.order_arm(
                    repo, target, tests, n, t, epoch, opts.order_seed, py, jobs, tick
                ),
            )
        )
    if "hashseed" in opts.arms:
        plan.append(
            (
                "hashseed",
                n,
                f"{n} runs, PYTHONHASHSEED 1..{n}",
                lambda tick: arms_mod.hashseed_arm(repo, target, n, t, epoch, py, jobs, tick),
            )
        )
    if "timezone" in opts.arms:
        if not arms_mod.tz_supported(py):
            # Not a platform guess: TZ only moves local time where time.tzset
            # exists. On Windows the Olson names are ignored while TZ=UTC shifts by
            # an hour, so an arm built on it can flip a test and then blame
            # "timezone" for something that reproduces nowhere the user runs it.
            say("timezone: skipped (TZ does not move local time on this platform)")
        else:
            plan.append(
                (
                    "timezone",
                    n,
                    f"{n} runs, TZ varied",
                    lambda tick: arms_mod.timezone_arm(repo, target, n, t, epoch, py, jobs, tick),
                )
            )
    if "locale" in opts.arms:
        if not arms_mod.locale_supported(py):
            say("locale: skipped (LANG and LC_ALL do not reach the locale on this platform)")
        else:
            plan.append(
                (
                    "locale",
                    n,
                    f"{n} runs, LANG and LC_ALL varied",
                    lambda tick: arms_mod.locale_arm(repo, target, n, t, epoch, py, jobs, tick),
                )
            )
    if "parallel" in opts.arms:
        if not arms_mod.xdist_available(repo, py):
            # Said, not skipped silently. Without xdist every run in the arm exits 4
            # and scores as unscoreable, which reads in the report as an arm that
            # found nothing rather than one that never ran.
            say("parallel: skipped (pytest-xdist is not installed in the target's environment)")
        else:
            plan.append(
                (
                    "parallel",
                    n,
                    f"{n} runs across worker processes",
                    lambda tick: arms_mod.parallel_arm(
                        repo, target, n, t, epoch, py, jobs=jobs, tick=tick
                    ),
                )
            )
    if "isolation" in opts.arms:
        # One pass is one process per test, so this is the expensive arm and the cost
        # is stated rather than discovered.
        plan.append(
            (
                "isolation",
                n * len(tests),
                f"{n} passes, {len(tests)} processes each",
                lambda tick: arms_mod.isolation_arm(
                    repo, target, n, t, epoch, py, tests, jobs, tick
                ),
            )
        )
    if "clock" in opts.arms:
        if not opts.freeze_clock:
            # Without freezing there is nothing to vary: the clock already varies in every
            # arm, so this one would be a second baseline wearing a different label.
            say("clock: skipped (the clock is not frozen, so it cannot be varied)")
        else:
            plan.append(
                (
                    "clock",
                    n,
                    f"{n} runs, frozen at a different date each time",
                    lambda tick: arms_mod.clock_arm(repo, target, n, t, py, jobs, tick),
                )
            )

    bar = _Progress(say, sum(units for _, units, _, _ in plan))
    built: list[Arm] = []
    for name, units, what, go in plan:
        bar.start(name, units, what)
        arm = go(bar.tick)
        built.append(arm)
        if name == "baseline" and not arm.runs:
            # Nothing to compare the other arms against, so do not spend their runs.
            inv = Investigation(arms=built, total_tests=len(tests))
            inv.problem = (
                f"none of the {arm.attempted} baseline runs could be scored, so nothing "
                "was examined. The first one failed like this:\n    "
                + arm.error.replace("\n", "\n    ")
            )
            inv.seconds = time.time() - started
            return inv

    inv = classify(built, tests)

    if opts.localise:
        from flake_detective.localise import localise as _localise
        from flake_detective.types import Cause

        order_flakes = [f for f in inv.flakes if f.cause is Cause.ORDER]
        for flake in order_flakes:
            say(f"localising: bisecting for what {flake.test_id} trips over")
            found = _localise(repo, flake.test_id, tests, t, py, epoch=epoch)
            flake.culprits = found.culprits
            say(f"  {found.describe()}  ({found.probes} runs)")

    inv.seconds = time.time() - started
    return inv
