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
