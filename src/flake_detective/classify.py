"""Decide what a test depends on, from which arm made it flip.

The rule is attribution by *exclusion*, and the order is what makes it sound:

1. A test that flips in the **baseline** is nondeterministic. Nothing more can be learned
   about it from the other arms, because everything flips it. It is classified there and
   removed from consideration, or every later arm would take credit for the same test.

2. Of what remains - tests stable under identical repetition - the arm that disagrees with
   the baseline names its dependency. It was stable when nothing changed and unstable when
   *that* changed.

3. The arms come in two families, and the difference matters:

   * **environment** arms - hashseed, clock, timezone, locale - each vary one independent
     thing about the world the suite runs in;
   * **interaction** arms - order, isolation, parallel - all vary *which other tests run
     before this one, in the same process*. Shuffling changes it, running alone removes it,
     and spreading the suite over workers changes it too.

   Two environment arms implicating one test is a genuine conflict: two independent causes,
   and picking one would be a guess. That, or an environment arm together with an
   interaction arm, is `UNKNOWN`. But several interaction arms implicating one test is not a
   conflict - it is the same fact seen three ways, and each one that agrees is corroboration.
   Calling that UNKNOWN made the classic finding *disappear* when more arms were switched on,
   which is backwards: more evidence must never produce a weaker answer.

## Which way an order dependence points

"Depends on other tests" has two directions with opposite fixes:

    broken by another test   passes on its own; fails after some test leaks state into it
    needs another test       fails on its own; passes only after some test creates state

The order arm alone cannot tell them apart - both fail in some shuffles and pass in others -
so without more evidence the finding is reported as an order dependence of *undetermined*
direction, and the advice says so rather than guessing. The isolation arm settles it, by
comparing the test's failure rate alone with its rate in the suite:

    fails more often alone than in the suite     needs another test
    fails less often alone than in the suite     broken by another test
    the same alone as in the suite               the default order already has it right:
                                                 passing there means something that can come
                                                 before it breaks it; failing there means
                                                 something that can come before it fixes it

`--localise` settles it too, later, by running the test alone and then bisecting.

A parallel flip on its own - order and isolation both quiet - is credited to parallelism:
something shared across processes, a port or a fixed path.

## What "implicated" has to mean

An arm implicates a test when the test flips within it **or** when its failure rate differs
from the baseline's. A test asserting `next(iter(some_set)) == "alpha"` passes under
`PYTHONHASHSEED=0` every time and fails under seeds 1-7 every time: it never flips *within*
an arm, and only the second reading sees it.

Rates are over the runs in which the test was actually observed to pass or fail. A test that
did not run - skipped, or stopped short - is not a pass, and is not counted at all.

## Why the baseline needs at least two runs

A single baseline run is not a control, because one run cannot flip. At one run per arm the
benchmark's nondeterministic test comes back labelled `clock` - it passed in the one baseline
run and failed in the one clock run. So a baseline with fewer than two observations of a test
establishes no cause for it. Instability is still reported; the cause is withheld.

A test that always fails is not flaky, and is reported separately as broken.
"""

from __future__ import annotations

from flake_detective.types import Arm, Cause, Flake, Investigation

ARM_CAUSE = {
    "order": Cause.ORDER,
    "hashseed": Cause.HASH_SEED,
    "clock": Cause.CLOCK,
    "timezone": Cause.TIMEZONE,
    "locale": Cause.LOCALE,
    "parallel": Cause.PARALLEL,
    "isolation": Cause.NEEDS_TEST,
}

#: Arms that all vary which other tests share the process with this one, and before it.
INTERACTION = ("order", "isolation", "parallel")

#: The end of an ORDER finding whose direction is unknown. `--localise` replaces it.
UNDIRECTED = (
    ": its result depends on which tests run before it. The order arm cannot say which "
    "way - broken by another test, or relying on one - and nothing else measured it"
)

_LABEL = {
    "order": "shuffled",
    "isolation": "run alone",
    "parallel": "across workers",
}


def _flipped(arm: Arm, test_id: str) -> bool:
    """Did this test both pass and fail within this arm?"""
    return arm.seen(test_id) > 1 and not arm.is_stable(test_id)


def _differs(arm: Arm, baseline: Arm, test_id: str) -> bool:
    """Did this arm land on a different failure rate than the baseline?"""
    if not arm.seen(test_id) or not baseline.seen(test_id):
        return False
    return abs(arm.rate(test_id) - baseline.rate(test_id)) > 1e-9


def _count(arm: Arm, test_id: str) -> str:
    return f"{arm.failures.get(test_id, 0)} of {arm.seen(test_id)}"


def _evidence(arm: Arm, baseline: Arm, test_id: str) -> str:
    if _flipped(arm, test_id):
        return (
            f"stable under identical repetition; failed {_count(arm, test_id)} runs when "
            f"{arm.description}"
        )
    return (
        f"failed {_count(baseline, test_id)} baseline runs and {_count(arm, test_id)} "
        f"when {arm.description} - consistent in both, at opposite results"
    )


def _interaction(
    test_id: str, implicated: list[Arm], baseline: Arm, by_name: dict[str, Arm], rates: dict
) -> Flake:
    """A test implicated only by interaction arms: order, isolation, parallel."""
    names = {a.name for a in implicated}
    counts = f"failed {_count(baseline, test_id)} baseline runs, " + ", ".join(
        f"{_count(a, test_id)} {_LABEL[a.name]}" for a in implicated
    )

    if names == {"parallel"}:
        return Flake(
            test_id,
            Cause.PARALLEL,
            counts + ": only spreading the suite across processes changes it, so something "
            "it uses is shared between processes",
            rates,
        )

    iso = by_name.get("isolation")
    b = baseline.rate(test_id)
    needs: bool | None = None
    if iso is not None and iso.seen(test_id):
        i = iso.rate(test_id)
        if i > b + 1e-9:
            needs = True
        elif i < b - 1e-9:
            needs = False
        else:
            # The same alone as in the suite, yet shuffling flips it. Passing in the
            # default order means something that can precede it breaks it; failing
            # there means something that can precede it is what makes it pass.
            needs = b > 0.5
        if "isolation" not in names:
            counts += f", {_count(iso, test_id)} {_LABEL['isolation']}"
    corroborated = " (the parallel arm, which also changes what runs before it, saw it change too)"
    extra = corroborated if "parallel" in names else ""

    if needs is True:
        return Flake(
            test_id,
            Cause.NEEDS_TEST,
            counts + ": it fails without tests that normally run before it, so it relies "
            "on state another test creates" + extra,
            rates,
        )
    if needs is False:
        return Flake(
            test_id,
            Cause.ORDER,
            counts + ": it passes on its own and fails after other tests, so another test "
            "leaves state behind that breaks it" + extra,
            rates,
        )
    return Flake(
        test_id,
        Cause.ORDER,
        counts + UNDIRECTED + extra,
        rates,
        directed=False,
    )


def classify(arms: list[Arm], tests: list[str]) -> Investigation:
    by_name = {a.name: a for a in arms}
    baseline = by_name.get("baseline")
    others = [a for a in arms if a.name != "baseline"]

    out = Investigation(arms=arms, total_tests=len(tests))

    for test_id in tests:
        ran = [a for a in arms if a.runs and a.seen(test_id)]
        rates = {a.name: a.rate(test_id) for a in ran}

        if not ran:
            # Collected, but never seen to pass or fail: skipped everywhere, or never
            # reached. Not a pass, and not evidence of anything.
            if any(a.runs for a in arms):
                out.unobserved.append(test_id)
            continue

        # Consistently broken in every arm that ran it: not flaky, just failing.
        if all(a.failures.get(test_id, 0) == a.seen(test_id) for a in ran):
            out.always_failed.append(test_id)
            continue

        if baseline and _flipped(baseline, test_id):
            out.flakes.append(
                Flake(
                    test_id,
                    Cause.NONDETERMINISM,
                    f"flipped with nothing changed: failed {_count(baseline, test_id)} "
                    "identical runs",
                    rates,
                )
            )
            continue

        if baseline is None or baseline.seen(test_id) < 2:
            # No usable control. One run cannot observe a flip, so a baseline of one is not
            # a control at all - and the failure it produces is the worst kind. At one run
            # per arm the benchmark's *nondeterministic* test came back labelled `clock`,
            # confidently, because it happened to pass in the single baseline run and fail
            # in the single clock run. Instability is still an observation worth reporting;
            # the cause is an inference, and there is nothing here to infer it from.
            unstable = (
                any(_flipped(a, test_id) or _differs(a, baseline, test_id) for a in others)
                if baseline
                else any(_flipped(a, test_id) for a in others)
            )
            if unstable:
                n = baseline.seen(test_id) if baseline else 0
                out.flakes.append(
                    Flake(
                        test_id,
                        Cause.UNKNOWN,
                        f"behaved differently across arms, but the baseline observed it in "
                        f"{n} run(s) - too few to observe a flip, so nondeterminism cannot "
                        f"be excluded and no cause is established",
                        rates,
                    )
                )
            continue

        implicated = [a for a in others if _flipped(a, test_id) or _differs(a, baseline, test_id)]
        if not implicated:
            continue

        env = [a for a in implicated if a.name not in INTERACTION]
        inter = [a for a in implicated if a.name in INTERACTION]

        if len(env) > 1 or (env and inter):
            out.flakes.append(
                Flake(
                    test_id,
                    Cause.UNKNOWN,
                    "behaved differently under independent perturbations ("
                    + ", ".join(a.name for a in implicated)
                    + "), so no single cause is established",
                    rates,
                )
            )
            continue

        if env:
            arm = env[0]
            out.flakes.append(
                Flake(
                    test_id,
                    ARM_CAUSE.get(arm.name, Cause.UNKNOWN),
                    _evidence(arm, baseline, test_id),
                    rates,
                )
            )
            continue

        out.flakes.append(_interaction(test_id, inter, baseline, by_name, rates))

    for f in out.flakes:
        f.errored = any(a.errors.get(f.test_id) for a in arms)
    return out
