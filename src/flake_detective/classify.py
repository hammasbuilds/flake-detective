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


# How many discordant observations it takes before a rate difference means anything.
#
# One does not. Measured on suite-auditor at five runs per arm: a test that fails 1.5% of
# the time from its own internal randomness came back `0 of 5 baseline, 1 of 5 shuffled`,
# and this returned True, so the test was reported as an ORDER dependence. It is not one.
# A single failure in one arm and none in the baseline is exactly what a low-rate
# intrinsic flake looks like, and calling it order dependence sends somebody to look for
# a leaking test that does not exist - the most expensive kind of wrong answer this tool
# can give.
MIN_DISCORDANT = 2


def _differs(arm: Arm, baseline: Arm, test_id: str) -> bool:
    """Did this arm land on a failure rate the baseline's cannot explain?

    Not "a different rate" - a rate that differs by more than `MIN_DISCORDANT`
    observations from what the baseline predicts for this arm's run count. Below that,
    the arm and the baseline are compatible with one underlying rate, which is the
    hypothesis a cause has to beat.
    """
    if not arm.seen(test_id) or not baseline.seen(test_id):
        return False
    if abs(arm.rate(test_id) - baseline.rate(test_id)) <= 1e-9:
        return False
    observed = arm.failures.get(test_id, 0)
    expected = baseline.rate(test_id) * arm.seen(test_id)
    return abs(observed - expected) >= MIN_DISCORDANT - 1e-9


def _any_rate_difference(arm: Arm, baseline: Arm, test_id: str) -> bool:
    """Did this arm land on ANY different failure rate than the baseline?

    The reporting question, not the attribution one. "Did this test behave differently
    anywhere" needs the loose comparison - a test that did is worth telling somebody
    about even when nothing can be blamed - while "was this arm the cause" needs
    `_differs`, which asks for enough discordant observations to beat one underlying
    rate. Using the strict test for both made a test that behaved differently vanish
    from the report instead of appearing with no cause.
    """
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


def _masked_by(arms: list[Arm], baseline: Arm | None, test_id: str) -> list[str]:
    """Arms whose evidence a saturated baseline makes uninformative.

    Attribution is by exclusion - an arm counts when its rate DIFFERS from the baseline -
    and that reasoning needs the baseline to have room to differ. A test that fails in
    EVERY baseline run cannot fail more often under any arm, so an arm sitting at the same
    rate has not been cleared; it has not been asked. Those arms are named so a second
    cause is reported as unruled-out rather than as absent.

    Only when the baseline is saturated at 1.0. A baseline of 0.0 is the ordinary case and
    leaves every arm free to rise above it.
    """
    if baseline is None or not baseline.seen(test_id):
        return []
    if baseline.rate(test_id) != 1.0:
        return []
    return sorted(
        a.name
        for a in arms
        if a.name != "baseline" and a.seen(test_id) and a.rate(test_id) == 1.0
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
                any(
                    _flipped(a, test_id) or _any_rate_difference(a, baseline, test_id)
                    for a in others
                )
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

        # Implicated means "this arm's failure count is one the baseline's rate cannot
        # explain". Flipping WITHIN an arm used to be enough on its own, and it is not:
        # a test that fails 1 of 5 shuffled runs and 0 of 5 baseline runs has flipped
        # inside the order arm, and is equally explained by a low intrinsic failure rate
        # the baseline did not happen to see. Measured on suite-auditor, where exactly
        # that was reported as ORDER and the test turns out to fail 1.5% of the time
        # alone. `_differs` carries the count test; `_flipped` still implicates when
        # there is no baseline observation to compare against.
        # Reaching here means the baseline observed this test at least twice, so there
        # is always a rate to compare against and `_differs` is the whole rule. (A
        # baseline that never saw the test is handled above, as UNKNOWN: with no control
        # there is nothing to infer a cause from. An earlier version of this line carried
        # a `not baseline.seen(...)` clause for that case, which was unreachable.)
        implicated = [a for a in others if _differs(a, baseline, test_id)]
        if not implicated:
            # Nothing is implicated strongly enough to name - but if the test behaved
            # differently somewhere, saying nothing is wrong. It is flaky and the cause
            # is not established, which is a different statement from "not flaky", and
            # the one that used to be given as ORDER.
            nearly = [
                a for a in others if _any_rate_difference(a, baseline, test_id)
            ]
            if nearly:
                out.flakes.append(
                    Flake(
                        test_id,
                        Cause.UNKNOWN,
                        "failed "
                        + _count(baseline, test_id)
                        + " baseline runs and "
                        + ", ".join(
                            f"{_count(a, test_id)} {_LABEL.get(a.name, a.name)}"
                            for a in nearly
                        )
                        + f": a difference of fewer than {MIN_DISCORDANT} observations, "
                        "which one underlying failure rate explains. More runs would "
                        "separate a cause from plain nondeterminism",
                        rates,
                    )
                )
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

        # Arms a saturated baseline left unasked. Computed once and attached to whichever
        # finding is made below, because the caveat is about the evidence, not the cause.
        masked = _masked_by(ran, baseline, test_id)

        if env:
            arm = env[0]
            found = Flake(
                test_id,
                ARM_CAUSE.get(arm.name, Cause.UNKNOWN),
                _evidence(arm, baseline, test_id),
                rates,
            )
            found.masked_arms = [name for name in masked if name != arm.name]
            out.flakes.append(found)
            continue

        found = _interaction(test_id, inter, baseline, by_name, rates)
        found.masked_arms = [name for name in masked if name not in {a.name for a in inter}]
        out.flakes.append(found)

    for f in out.flakes:
        f.errored = any(a.errors.get(f.test_id) for a in arms)
    return out
