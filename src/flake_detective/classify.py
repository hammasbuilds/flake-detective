"""Decide what a test depends on, from which arm made it flip.

The rule is attribution by *exclusion*, and the order is what makes it sound:

1. A test that flips in the **baseline** is nondeterministic. Nothing more can be learned
   about it from the other arms, because everything flips it. It is classified there and
   removed from consideration, or every later arm would take credit for the same test.

2. Of what remains - tests stable under identical repetition - the arm that disagrees with
   the baseline names its dependency. It was stable when nothing changed and unstable when
   *that* changed.

3. A test that is implicated by more than one remaining arm is `UNKNOWN`, not the first
   match. Reporting the first would be a guess dressed as a finding, and a wrong cause sends
   somebody to the wrong file.

## What "implicated" has to mean

The obvious reading - *the arm made it flip* - misses the clearest evidence there is.

A test asserting `next(iter(some_set)) == "alpha"` passes under `PYTHONHASHSEED=0` every
single time, and under seeds 1-7 fails every single time. It never flips *within* an arm. It
is perfectly stable in both, at opposite values, and a within-arm test sees nothing at all.

So an arm implicates a test when it flips **or** when its failure rate differs from the
baseline's. Both are the same statement - the test behaves differently once that variable
moves - and the second is the stronger finding, because it is not a coincidence of sampling.

## Why the baseline needs at least two runs

A single baseline run is not a control, because one run cannot flip. Run the benchmark at one
run per arm and its *nondeterministic* test comes back labelled `clock` - it passed in the
one baseline run and failed in the one clock run, and every rule above is satisfied. The
label is confident and wrong.

So a baseline of fewer than two usable runs establishes no cause at all. Instability across
arms is still reported, because it was observed; the cause is withheld, because it was not.

A test that always fails is not flaky. Its behaviour is perfectly consistent, and folding it
into the flake count would inflate the number with broken tests.
"""

from __future__ import annotations

from flake_detective.types import Arm, Cause, Flake, Investigation

ARM_CAUSE = {
    "order": Cause.ORDER,
    "hashseed": Cause.HASH_SEED,
    "clock": Cause.CLOCK,
}


def _flipped(arm: Arm, test_id: str) -> bool:
    """Did this test both pass and fail within this arm?"""
    return arm.runs > 1 and not arm.is_stable(test_id)


def _differs(arm: Arm, baseline: Arm, test_id: str) -> bool:
    """Did this arm land on a different failure rate than the baseline?"""
    if not arm.runs or not baseline.runs:
        return False
    return abs(arm.rate(test_id) - baseline.rate(test_id)) > 1e-9


def _evidence(arm: Arm, baseline: Arm, test_id: str) -> str:
    n, runs = arm.failures.get(test_id, 0), arm.runs
    if _flipped(arm, test_id):
        return (
            f"stable under identical repetition; failed {n} of {runs} runs when {arm.description}"
        )
    b = baseline.failures.get(test_id, 0)
    return (
        f"failed {b} of {baseline.runs} baseline runs and {n} of {runs} "
        f"when {arm.description} - consistent in both, at opposite results"
    )


def classify(arms: list[Arm], tests: list[str]) -> Investigation:
    by_name = {a.name: a for a in arms}
    baseline = by_name.get("baseline")
    others = [a for a in arms if a.name != "baseline"]

    out = Investigation(arms=arms, total_tests=len(tests))

    for test_id in tests:
        rates = {a.name: a.rate(test_id) for a in arms}

        # Consistently broken in every arm that ran it: not flaky, just failing.
        ran = [a for a in arms if a.runs]
        if ran and all(a.failures.get(test_id, 0) == a.runs for a in ran):
            out.always_failed.append(test_id)
            continue

        if baseline and _flipped(baseline, test_id):
            out.flakes.append(
                Flake(
                    test_id,
                    Cause.NONDETERMINISM,
                    f"flipped with nothing changed: failed "
                    f"{baseline.failures.get(test_id, 0)} of {baseline.runs} identical runs",
                    rates,
                )
            )
            continue

        if baseline is None or baseline.runs < 2:
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
                n = baseline.runs if baseline else 0
                out.flakes.append(
                    Flake(
                        test_id,
                        Cause.UNKNOWN,
                        f"behaved differently across arms, but the baseline scored {n} "
                        f"usable run(s) - too few to observe a flip, so nondeterminism "
                        f"cannot be excluded and no cause is established",
                        rates,
                    )
                )
            continue

        culprits = [a for a in others if _flipped(a, test_id) or _differs(a, baseline, test_id)]
        if not culprits:
            continue

        if len(culprits) > 1:
            out.flakes.append(
                Flake(
                    test_id,
                    Cause.UNKNOWN,
                    "behaved differently under more than one perturbation ("
                    + ", ".join(a.name for a in culprits)
                    + "), so no single cause is established",
                    rates,
                )
            )
            continue

        arm = culprits[0]
        out.flakes.append(
            Flake(
                test_id,
                ARM_CAUSE.get(arm.name, Cause.UNKNOWN),
                _evidence(arm, baseline, test_id),
                rates,
            )
        )
    return out
