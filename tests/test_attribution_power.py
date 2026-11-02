"""The power of the *attribution* criterion, which is not the power of detection.

Everything in `power.py` before `attribution_probability` is about the flip criterion: did
a test both pass and fail somewhere. That is what decides whether a test is **reported**.
Naming a cause is a second, stricter question, and until this was computed the module
bounded one criterion while the tool shipped two - so a report could say "no flake above
34.9%" and leave the reader to assume the same budget could also blame an arm.

`classify._differs` blames an arm when its failure count differs from what the baseline's
rate predicts for the same run count by at least `MIN_DISCORDANT`. With equal run counts
the prediction is the baseline's own count, so the criterion is exactly
`|X_arm - X_baseline| >= MIN_DISCORDANT` over two independent binomials. That is computed
by convolution, not approximated, and the tests below check it against the cases where the
answer is known by hand as well as against the shape of the whole curve.

The consequence is the reason the function exists: with a clean baseline and the default
threshold of two, an arm must fail **twice** before it can be blamed, so attribution power
at a low rate is roughly the square of detection power. At seven runs against a 2% flake,
13.2% of such flakes are seen and 0.8% can be attributed.
"""

from __future__ import annotations

import pytest

from flake_detective import power
from flake_detective.classify import MIN_DISCORDANT

# -------------------------------------------------------------- against known answers


def test_an_arm_that_never_fails_can_never_be_blamed():
    assert power.attribution_probability(0.0, 7) == 0.0
    assert power.attribution_probability(0.0, 500) == 0.0


def test_an_arm_that_always_fails_against_a_clean_baseline_is_always_blamed():
    """Seven failures against zero is a difference of seven, well past the threshold."""
    assert power.attribution_probability(1.0, 7) == pytest.approx(1.0)


def test_two_arms_that_behave_identically_are_never_blamed():
    """Both failing every run is a difference of zero, whatever the run count."""
    assert power.attribution_probability(1.0, 7, 1.0) == pytest.approx(0.0)
    assert power.attribution_probability(0.0, 7, 0.0) == pytest.approx(0.0)


def test_the_clean_baseline_case_is_the_chance_of_failing_twice():
    """With a baseline at zero the criterion is X_arm >= MIN_DISCORDANT, by hand."""
    assert MIN_DISCORDANT == 2
    for rate, runs in ((0.02, 7), (0.1, 5), (0.3, 11)):
        passes = 1.0 - rate
        exactly_none = passes**runs
        exactly_one = runs * rate * passes ** (runs - 1)
        expected = 1.0 - exactly_none - exactly_one
        assert power.attribution_probability(rate, runs) == pytest.approx(expected, abs=1e-12)


def test_a_threshold_of_one_reduces_to_seeing_a_single_failure():
    """A sanity check on the convolution, not an identity with the flip criterion.

    At `min_discordant=1` and a clean baseline the criterion is X_arm >= 1, whose
    probability is 1-(1-p)^n. The flip criterion is 1-(1-p)^n-p^n, so the two agree to
    within p^n - very close at a low rate and visibly apart at a high one. Asserting
    equality would be asserting that p^n is zero.
    """
    for rate, runs in ((0.02, 7), (0.1, 7)):
        got = power.attribution_probability(rate, runs, 0.0, 1)
        assert got == pytest.approx(1.0 - (1.0 - rate) ** runs, abs=1e-12)
        assert got == pytest.approx(power.flip_probability(rate, runs), abs=rate**runs + 1e-12)
    # At a coin flip they must NOT agree: p^n is 1/128 there.
    assert power.attribution_probability(0.5, 7, 0.0, 1) > power.flip_probability(0.5, 7) + 1e-3


# ------------------------------------------------------------------------- the shape


def test_attribution_is_never_easier_than_seeing_the_arm_fail_at_all():
    """Blaming an arm needs two failures in it; seeing one needs one.

    The comparison is against `1-(1-p)^n`, the chance the arm fails at least once, and
    **not** against `flip_probability`. The two describe different scenarios and the
    ordering does not hold between them: `flip_probability(0.9, 3)` is 0.27, because a test
    failing nine runs in ten rarely passes as well within three runs, while an arm at 0.9
    against a clean baseline is blamed 97.2% of the time. An earlier version of this test
    asserted that ordering and was simply wrong - attribution is bounded by the arm's own
    failure count, not by whether a single rate produces a flip.
    """
    for rate in (0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 0.9):
        for runs in (3, 7, 11, 59):
            at_least_one = 1.0 - (1.0 - rate) ** runs
            assert power.attribution_probability(rate, runs) <= at_least_one + 1e-12, (rate, runs)


def test_more_runs_never_lower_the_chance_of_blaming_an_arm():
    for rate in (0.02, 0.1, 0.5):
        values = [power.attribution_probability(rate, n) for n in range(1, 40)]
        assert values == sorted(values), rate


def test_a_higher_rate_never_lowers_it_either():
    """Monotone to within floating point: the convolution sums to 0.9999999999999999."""
    for runs in (5, 7, 29):
        values = [power.attribution_probability(r / 100, runs) for r in range(0, 101, 5)]
        for earlier, later in zip(values, values[1:], strict=False):
            assert later >= earlier - 1e-12, (runs, earlier, later)


def test_the_gap_widens_as_the_rate_falls():
    """The reason this matters: at a low rate attribution is far weaker, not slightly.

    Measured against the chance the arm fails at least once, which is the thing attribution
    is a stricter version of.
    """
    ratios = {}
    for rate in (0.5, 0.1, 0.02):
        at_least_one = 1.0 - (1.0 - rate) ** 7
        ratios[rate] = power.attribution_probability(rate, 7) / at_least_one
    assert ratios[0.5] > ratios[0.1] > ratios[0.02]
    assert ratios[0.02] < 0.1, ratios


def test_the_published_figures_for_the_default_budget():
    assert power.flip_probability(0.02, 7) == pytest.approx(0.1319, abs=5e-5)
    assert power.attribution_probability(0.02, 7) == pytest.approx(0.0079, abs=5e-5)
    assert power.runs_for_power(0.02) == 149
    assert power.attribution_runs_for_power(0.02) == 236


def test_naming_a_cause_always_costs_at_least_as_many_runs_as_seeing_one():
    for rate in (0.01, 0.02, 0.05, 0.1, 0.25, 0.5):
        seeing = power.runs_for_power(rate)
        blaming = power.attribution_runs_for_power(rate)
        assert blaming is not None, rate
        assert seeing is not None, rate
        assert blaming >= seeing, (rate, seeing, blaming)


def test_an_unreachable_budget_is_none_rather_than_the_cap():
    """A budget nobody would run is not an answer, and the cap would read as one."""
    assert power.attribution_runs_for_power(0.02, cap=10) is None
    assert power.attribution_runs_for_power(0.5, cap=10) == 8


def test_the_threshold_comes_from_the_classifier_rather_than_a_copy():
    """A second copy here would bound a threshold the tool does not use."""
    assert power._min_discordant() == MIN_DISCORDANT
    # And the default really is the classifier's, not an independently typed 2.
    assert power.attribution_probability(0.3, 9) == power.attribution_probability(
        0.3, 9, 0.0, MIN_DISCORDANT
    )


def test_a_rate_outside_zero_to_one_is_refused():
    for bad in (-0.1, 1.1):
        with pytest.raises(ValueError):
            power.attribution_probability(bad, 7)
        with pytest.raises(ValueError):
            power.attribution_probability(0.1, 7, bad)
    with pytest.raises(ValueError):
        power.attribution_probability(0.1, 0)


# ---------------------------------------------------------- what the report says with it


def test_the_power_table_carries_both_criteria():
    rows = power.power_table(7)
    assert rows, "the table is empty"
    for row in rows:
        assert "flip_power_at_this_budget" in row
        assert "attribution_power_at_this_budget" in row
        assert row["attribution_power_at_this_budget"] <= row["flip_power_at_this_budget"]
        assert row["runs_for_target_attribution_power"] >= row["runs_for_target_flip_power"]


def test_a_clean_report_prints_the_no_cause_column():
    from flake_detective import report
    from flake_detective.types import Arm, Investigation

    inv = Investigation(
        arms=[Arm("baseline", "identical conditions, repeated", runs=7, attempted=7)],
        total_tests=12,
    )
    text = report.text(inv)
    assert "no cause" in text
    # 99.2% of one-in-fifty flakes could not have been attributed at this budget.
    assert "99.2%" in text
    assert "Naming its cause needs 236" in text


def test_an_unattributable_finding_says_how_many_runs_would_settle_it():
    """The message used to say only that more runs would help."""
    from flake_detective.classify import _how_many_runs_would_separate
    from flake_detective.types import Arm

    arm = Arm("order", "shuffled", runs=7, failures={"t": 1}, observed={"t": 7}, tracked=True)
    message = _how_many_runs_would_separate([arm], "t")
    assert str(power.attribution_runs_for_power(1 / 7)) in message
    assert "14.3%" in message
    # And it says the estimate rests on the runs that produced it.
    assert "rests on the same few runs" in message


def test_an_arm_with_no_usable_rate_falls_back_to_the_vague_sentence():
    """A rate of 0 or 1 gives no budget to invert, and inventing one would be worse."""
    from flake_detective.classify import _how_many_runs_would_separate
    from flake_detective.types import Arm

    arm = Arm("order", "shuffled", runs=7, failures={}, observed={"t": 7}, tracked=True)
    assert _how_many_runs_would_separate([arm], "t") == (
        "More runs would separate a cause from plain nondeterminism"
    )
