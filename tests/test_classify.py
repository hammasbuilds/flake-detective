"""The attribution rules, on hand-built arms.

These are pure-function tests: no suite is run. Building `Arm`s directly is what makes the
awkward shapes reachable - an arm that scored zero runs, a test that is stable in two arms at
opposite results - which are exactly the cases the classifier got wrong the first time.
"""

from __future__ import annotations

import pytest

from flake_detective.classify import classify
from flake_detective.types import Arm, Cause

TESTS = ["t.py::a", "t.py::b", "t.py::c"]


def arm(name: str, runs: int, **failures: int) -> Arm:
    return Arm(name, name, runs, {f"t.py::{k}": v for k, v in failures.items()})


def causes(inv):
    return {f.test_id: f.cause for f in inv.flakes}


def test_a_test_that_never_fails_is_not_reported():
    inv = classify([arm("baseline", 5), arm("order", 5)], TESTS)
    assert inv.flakes == []


def test_flipping_in_the_baseline_is_nondeterminism():
    inv = classify([arm("baseline", 5, a=2), arm("order", 5, a=2)], TESTS)
    assert causes(inv)["t.py::a"] is Cause.NONDETERMINISM


def test_the_baseline_wins_even_when_another_arm_also_flips():
    """Otherwise every arm takes credit for the same nondeterministic test.

    A test that flips under identical conditions also flips when the order changes, so
    without this precedence the tool would report a cause for it - and the cause would be
    whichever arm happened to be checked first.
    """
    inv = classify([arm("baseline", 5, a=2), arm("order", 5, a=3), arm("hashseed", 5, a=1)], TESTS)
    assert causes(inv)["t.py::a"] is Cause.NONDETERMINISM


def test_one_arm_flipping_names_the_cause():
    inv = classify([arm("baseline", 5), arm("order", 5, a=2), arm("hashseed", 5)], TESTS)
    assert causes(inv)["t.py::a"] is Cause.ORDER


@pytest.mark.parametrize(
    "name,cause",
    [("order", Cause.ORDER), ("hashseed", Cause.HASH_SEED), ("clock", Cause.CLOCK)],
)
def test_each_arm_maps_to_its_own_cause(name, cause):
    inv = classify([arm("baseline", 5), arm(name, 5, a=2)], TESTS)
    assert causes(inv)["t.py::a"] is cause


def test_two_arms_is_unknown_not_the_first_one():
    inv = classify([arm("baseline", 5), arm("order", 5, a=2), arm("hashseed", 5, a=3)], TESTS)
    assert causes(inv)["t.py::a"] is Cause.UNKNOWN


def test_stable_in_both_arms_at_opposite_results_is_still_a_finding():
    """The bug the benchmark caught.

    A test asserting `next(iter(a_set)) == "alpha"` passes 5/5 under PYTHONHASHSEED=0 and
    fails 5/5 under other seeds. It never flips *within* an arm, and a within-arm check saw
    nothing at all - the clearest evidence of hash dependence there is, missed entirely.
    """
    inv = classify([arm("baseline", 5), arm("hashseed", 5, a=5)], TESTS)
    assert causes(inv)["t.py::a"] is Cause.HASH_SEED


def test_failing_everywhere_is_broken_not_flaky():
    inv = classify([arm("baseline", 5, a=5), arm("order", 5, a=5)], TESTS)
    assert inv.flakes == []
    assert inv.always_failed == ["t.py::a"]


def test_an_arm_that_scored_no_runs_accuses_nobody():
    """A crashed arm is absence of evidence, not evidence of stability."""
    inv = classify([arm("baseline", 5), arm("order", 0)], TESTS)
    assert inv.flakes == []


def test_a_baseline_that_scored_no_runs_yields_no_attribution():
    """Without a control, "it flipped in the order arm" does not mean order caused it.

    The test may well have flipped with nothing changed at all - which is precisely what
    the baseline exists to rule out, and it did not run. The flip is still reported, since
    it was observed; the cause is not, since it was not.
    """
    inv = classify([arm("baseline", 0), arm("order", 5, a=2)], TESTS)
    assert causes(inv) == {"t.py::a": Cause.UNKNOWN}
    assert "too few to observe a flip" in inv.flakes[0].evidence


def test_rates_are_recorded_for_every_arm():
    inv = classify([arm("baseline", 4), arm("order", 4, a=1)], TESTS)
    assert inv.flakes[0].rates == {"baseline": 0.0, "order": 0.25}


def test_every_flake_carries_a_fix_and_evidence():
    inv = classify([arm("baseline", 5), arm("order", 5, a=2)], TESTS)
    f = inv.flakes[0]
    assert f.fix and f.evidence
    assert "2 of 5" in f.evidence


def test_counts_by_cause():
    inv = classify([arm("baseline", 5, c=2), arm("order", 5, a=2), arm("hashseed", 5, b=5)], TESTS)
    assert inv.by_cause() == {"order": 1, "hash-seed": 1, "nondeterminism": 1}


def test_a_one_run_baseline_establishes_no_cause():
    """The sweep is what caught this.

    At one run per arm the *nondeterministic* fixture test came back labelled `clock`: it
    passed in the single baseline run and failed in the single clock run, which satisfies
    every attribution rule. One run cannot flip, so a baseline of one is not a control -
    it just looks like one.
    """
    inv = classify([arm("baseline", 1), arm("clock", 1, a=1)], TESTS)
    assert causes(inv) == {"t.py::a": Cause.UNKNOWN}
    assert "too few to observe a flip" in inv.flakes[0].evidence


def test_two_runs_is_enough_to_attribute():
    inv = classify([arm("baseline", 2), arm("clock", 2, a=2)], TESTS)
    assert causes(inv) == {"t.py::a": Cause.CLOCK}


class TestASaturatedBaselineHidesOtherCauses:
    """Attribution by exclusion needs the baseline room to differ.

    An arm is implicated when its failure rate DIFFERS from the baseline's. That is sound
    while the baseline can move. When a dominant cause fails the test in EVERY baseline
    run, every other arm also fails it in every run, nothing differs, and no second cause
    can be seen.

    Reproduced on a test that is both order-dependent and clock-dependent: baseline 1.0,
    order 0.44, isolation 0.0, clock 1.0 - reported as plain ORDER with the fix "reset that
    state in a fixture". Follow it and the test still fails half the time, and the next run
    gives the same confident single answer.

    This does not find the second cause; that needs a re-run after the first is fixed. It
    reports that one is not ruled out.
    """

    def _arms(self, rates: dict[str, float], runs: int = 10):
        """Arms whose rate for one test is as given."""
        from flake_detective.types import Arm

        test = "t.py::test_one"
        out = []
        for name, rate in rates.items():
            arm = Arm(name=name, description=name, runs=runs)
            arm.observed[test] = runs
            arm.failures[test] = round(rate * runs)
            out.append(arm)
        return out, test

    def test_a_masked_arm_is_named_when_the_baseline_is_saturated(self):
        from flake_detective.classify import classify

        arms, test = self._arms({"baseline": 1.0, "order": 0.4, "hashseed": 1.0, "clock": 1.0})
        inv = classify(arms, [test])
        assert len(inv.flakes) == 1
        found = inv.flakes[0]
        assert found.cause.value == "order"
        assert set(found.masked_arms) == {"clock", "hashseed"}, (
            "arms sitting at a saturated baseline were treated as ruled out"
        )
        row = found.as_row()
        assert row["second_cause_possible"] is True
        assert set(row["masked_arms"]) == {"clock", "hashseed"}

    def test_nothing_is_masked_when_the_baseline_has_room_to_move(self):
        """The ordinary case: a baseline of 0.0 leaves every arm free to rise above it."""
        from flake_detective.classify import classify

        arms, test = self._arms({"baseline": 0.0, "order": 0.4, "hashseed": 0.0, "clock": 0.0})
        inv = classify(arms, [test])
        assert len(inv.flakes) == 1
        assert inv.flakes[0].masked_arms == []
        assert "masked_arms" not in inv.flakes[0].as_row()

    def test_the_arm_that_was_blamed_is_not_listed_as_masked(self):
        """It differed from the baseline, so it was asked and answered."""
        from flake_detective.classify import classify

        arms, test = self._arms({"baseline": 1.0, "clock": 0.3, "hashseed": 1.0})
        inv = classify(arms, [test])
        found = inv.flakes[0]
        assert found.cause.value == "clock"
        assert found.masked_arms == ["hashseed"]

    def test_the_report_tells_the_user(self):
        """Computing it and keeping it in memory is the defect, not the fix."""
        from flake_detective.classify import classify
        from flake_detective.report import text

        arms, test = self._arms({"baseline": 1.0, "order": 0.4, "hashseed": 1.0, "clock": 1.0})
        rendered = text(classify(arms, [test]))
        assert "were not ruled out" in rendered
        assert "clock, hashseed" in rendered


def test_one_discordant_observation_names_no_cause():
    """The case that sent somebody looking for a leaking test that does not exist.

    flake-detective was run over suite-auditor and reported
    `test_a_function_that_disagrees_with_its_own_repeat_is_not_a_gap` as an ORDER
    dependence: 0 of 5 baseline runs, 1 of 5 shuffled. Running that assertion 200 times
    alone, in one process, with no other test involved, it fails **1.5% of the time** -
    so it is nondeterminism, and the order arm simply happened to be the one that saw it.

    Flipping within an arm used to implicate that arm on its own. One failure there and
    none in the baseline is equally explained by a low intrinsic rate the baseline did
    not happen to observe, and naming a cause from it is the most expensive wrong answer
    this tool can give: it sends a reader to look for a test that leaks state.
    """
    inv = classify([arm("baseline", 5), arm("order", 5, a=1)], TESTS)
    assert causes(inv) == {"t.py::a": Cause.UNKNOWN}
    assert "fewer than 2 observations" in inv.flakes[0].evidence
    # Still reported. "Not established" is not "not flaky", and dropping it would be
    # worse than naming the wrong cause.
    assert inv.by_cause() == {"unknown": 1}


def test_two_discordant_observations_are_enough():
    """The threshold has to let a real cause through, or the tool finds nothing."""
    inv = classify([arm("baseline", 5), arm("order", 5, a=2)], TESTS)
    assert causes(inv) == {"t.py::a": Cause.ORDER}


def test_a_rate_difference_is_measured_against_the_baseline_not_against_zero():
    """A noisy baseline raises the bar, which is the point of having one.

    Baseline 2 of 5 and clock 3 of 5 is one extra failure, not three: the arm has to beat
    what the baseline's own rate predicts for it, not beat zero.
    """
    inv = classify([arm("baseline", 5, a=2), arm("clock", 5, a=3)], TESTS)
    assert causes(inv)["t.py::a"] == Cause.NONDETERMINISM, (
        "a baseline that flips on its own is nondeterminism, whatever the arms then show"
    )


def test_no_baseline_observation_means_no_cause_however_the_arms_behaved():
    """With no control there is nothing to infer from, and the tool says so.

    `observed` is a dict of test id -> runs that saw it, and `seen()` consults it only
    when `tracked` is set. The first version of this test assigned a SET and left
    `tracked` alone, so the baseline still reported five observations and the assertion
    passed through a different branch entirely - green, and about nothing.

    Written to assert that a flip inside an arm would be credited to that arm when the
    baseline never saw the test. It is not, and should not be: the "no usable control"
    branch reports UNKNOWN first. The clause added to the implication rule for that case
    was unreachable and is gone.
    """
    base = arm("baseline", 5)
    base.tracked = True
    base.observed = {t: 5 for t in TESTS if t != "t.py::a"}
    other = arm("order", 5, a=1)
    other.tracked = True
    other.observed = dict.fromkeys(TESTS, 5)
    assert base.seen("t.py::a") == 0, "the baseline must not have observed this test"

    inv = classify([base, other], TESTS)
    assert causes(inv).get("t.py::a") == Cause.UNKNOWN
    assert "too few to observe a flip" in inv.flakes[0].evidence
