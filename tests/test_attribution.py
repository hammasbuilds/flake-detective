"""Attribution across arms: interaction arms corroborate, environment arms conflict.

Before this, switching on more arms made the classic finding vanish: with --arms all a
plain polluter/victim pair came back UNKNOWN (order, parallel, isolation), because every
arm that agreed was counted as a rival explanation. More evidence must never give a weaker
answer. And a test that NEEDS another test's state was labelled "a previous test leaves
state behind" - the opposite direction, with the opposite fix.
"""

from __future__ import annotations

from flake_detective.classify import classify
from flake_detective.report import ORDER, as_json, text
from flake_detective.types import ORDER_UNDIRECTED_FIX, Arm, Cause, Flake, Investigation

T = "t.py::victim"


def _one(*arms: Arm) -> Flake:
    inv = classify(list(arms), [T])
    assert len(inv.flakes) == 1, inv.flakes
    return inv.flakes[0]


def test_audit_case_all_arms_on_a_polluted_victim_is_order_not_unknown():
    """The numbers the audit saw: fails in the suite order, sometimes passes shuffled,
    fails across workers, passes alone every time."""
    f = _one(
        Arm("baseline", "b", runs=4, failures={T: 4}),
        Arm("order", "o", runs=4, failures={T: 3}),
        Arm("hashseed", "h", runs=4, failures={T: 4}),
        Arm("parallel", "p", runs=4, failures={T: 4}),
        Arm("isolation", "i", runs=4, failures={}),
        Arm("clock", "c", runs=4, failures={T: 4}),
    )
    assert f.cause is Cause.ORDER
    assert f.directed
    assert "passes on its own" in f.evidence
    assert "leaves state behind" in f.fix


def test_parallel_agreeing_with_order_is_corroboration():
    f = _one(
        Arm("baseline", "b", runs=4, failures={}),
        Arm("order", "o", runs=4, failures={T: 2}),
        Arm("parallel", "p", runs=4, failures={T: 3}),
    )
    assert f.cause is Cause.ORDER
    assert "parallel arm" in f.evidence and "saw it change too" in f.evidence


def test_a_test_that_needs_another_is_not_called_polluted():
    """test_f_needs_env: passes in the suite, fails alone every time, fails in the
    shuffles that put its enabler after it."""
    f = _one(
        Arm("baseline", "b", runs=4, failures={}),
        Arm("order", "o", runs=4, failures={T: 1}),
        Arm("isolation", "i", runs=4, failures={T: 4}),
    )
    assert f.cause is Cause.NEEDS_TEST
    assert "relies on state another test creates" in f.evidence
    assert "leaves state behind" not in f.fix


def test_needing_a_test_that_normally_runs_after_it():
    """Fails in the suite order AND alone, passes in some shuffles: something that
    usually runs after it is what makes it pass."""
    f = _one(
        Arm("baseline", "b", runs=4, failures={T: 4}),
        Arm("order", "o", runs=4, failures={T: 2}),
        Arm("isolation", "i", runs=4, failures={T: 4}),
    )
    assert f.cause is Cause.NEEDS_TEST


def test_polluted_by_a_test_that_normally_runs_after_it():
    """Passes in the suite order and alone, fails in some shuffles."""
    f = _one(
        Arm("baseline", "b", runs=4, failures={}),
        Arm("order", "o", runs=4, failures={T: 2}),
        Arm("isolation", "i", runs=4, failures={}),
    )
    assert f.cause is Cause.ORDER and f.directed


def test_without_isolation_the_direction_is_left_open_and_the_advice_says_so():
    f = _one(
        Arm("baseline", "b", runs=4, failures={}),
        Arm("order", "o", runs=4, failures={T: 2}),
    )
    assert f.cause is Cause.ORDER
    assert not f.directed
    assert f.fix == ORDER_UNDIRECTED_FIX
    assert "--localise" in f.fix
    assert f.as_row()["direction"] == "undetermined"


def test_parallel_alone_is_parallel_when_isolation_agrees_with_the_baseline():
    f = _one(
        Arm("baseline", "b", runs=4, failures={}),
        Arm("order", "o", runs=4, failures={}),
        Arm("parallel", "p", runs=4, failures={T: 2}),
        Arm("isolation", "i", runs=4, failures={}),
    )
    assert f.cause is Cause.PARALLEL


def test_two_environment_arms_are_a_real_conflict():
    f = _one(
        Arm("baseline", "b", runs=4, failures={}),
        Arm("hashseed", "h", runs=4, failures={T: 2}),
        Arm("clock", "c", runs=4, failures={T: 2}),
    )
    assert f.cause is Cause.UNKNOWN


def test_an_environment_arm_with_an_interaction_arm_is_unknown():
    f = _one(
        Arm("baseline", "b", runs=3, failures={}),
        Arm("order", "o", runs=3, failures={T: 1}),
        Arm("isolation", "i", runs=3, failures={T: 3}),
        Arm("clock", "c", runs=3, failures={T: 2}),
    )
    assert f.cause is Cause.UNKNOWN


def test_rates_count_only_the_runs_that_observed_the_test():
    """`addopts = -x` stopped runs before test_b; absence read as a pass and made an
    order-dependent test look nondeterministic (baseline 0.6). Five runs, observed in
    three, failed in all three: that is a stable failure, not a flip."""
    baseline = Arm(
        "baseline", "b", runs=5, failures={T: 3}, observed={T: 3, "t.py::x": 5}, tracked=True
    )
    assert baseline.seen(T) == 3
    assert baseline.rate(T) == 1.0
    assert baseline.is_stable(T)
    order = Arm("order", "o", runs=5, failures={T: 2}, observed={T: 5, "t.py::x": 5}, tracked=True)
    f = _one(baseline, order)
    assert f.cause is Cause.ORDER


def test_a_test_no_run_observed_is_reported_as_unjudged_not_as_passing():
    baseline = Arm("baseline", "b", runs=3, failures={}, observed={"t.py::x": 3}, tracked=True)
    inv = classify([baseline], [T, "t.py::x"])
    assert inv.unobserved == [T]
    assert not inv.flakes
    assert "never passed or failed" in text(inv)


def test_the_headline_counts_every_cause_it_lists():
    """Under --arms all the headline said "9 flaky tests:" and listed four: the report's
    cause list had five entries and four arms had been added since."""
    assert set(ORDER) == set(Cause)
    flakes = [Flake(f"t.py::{c.value}", c, "e") for c in Cause]
    inv = Investigation(
        arms=[Arm("baseline", "b", runs=2, attempted=2)], flakes=flakes, total_tests=9
    )
    out = text(inv)
    head = out.split("flaky tests:")[1].split("Rates are")[0]
    listed = sum(int(line.split()[0]) for line in head.splitlines() if line.strip())
    assert f"{len(Cause)} flaky tests:" in out
    assert listed == len(Cause), head
    assert set(as_json(inv)["by_cause"]) == {c.value for c in Cause}


def test_an_arm_that_never_saw_a_test_prints_a_dash_not_a_zero():
    inv = Investigation(
        arms=[Arm("baseline", "b", runs=2), Arm("order", "o", runs=2)],
        flakes=[Flake(T, Cause.NONDETERMINISM, "e", rates={"baseline": 0.5})],
        total_tests=1,
    )
    row = next(line for line in text(inv).splitlines() if line.startswith(T))
    assert row.split()[-2:] == ["0.5", "-"]


def test_a_suite_where_everything_was_skipped_is_unjudged_not_clean():
    """An arm whose runs observed nothing has an empty `observed` - which once meant
    "assume every run saw every test", and so read as seven clean passes each."""
    baseline = Arm("baseline", "b", runs=3, tracked=True)
    order = Arm("order", "o", runs=3, tracked=True)
    inv = classify([baseline, order], [T])
    assert inv.unobserved == [T]
