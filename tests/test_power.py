"""What a rerun budget can and cannot see, and the two places the arithmetic goes wrong.

`power` exists because a clean report is a claim about every test that did not flip, and
that claim had no bound on it. The module is short and the formulae are standard, so the
interesting tests are the ones that pin the distinctions:

* `observed_probability` against `flip_probability` - two questions, equal for a rare
  flake, different for a frequent one, and the report prints both because the gap is the
  point.
* `Sprt.expected_runs` inside the indifference region, where Wald's approximation divides
  a vanishing numerator by a vanishing drift. It returned **one run** at a 0.5% rate,
  between 164 at 0.1% and 64 at 2%, which is not a near-miss but the reverse of the truth.

The hand-worked values below were computed independently of the module, from
`1 - (1-p)^n - p^n` and Wald's boundaries, so a sign error in the module cannot agree with
them by construction.
"""

from __future__ import annotations

import math

import pytest

from flake_detective import power

# --------------------------------------------------------------- the two criteria differ


def test_flip_probability_matches_the_closed_form():
    for rate in (0.5, 0.25, 0.1, 0.02):
        for runs in (2, 3, 7, 29):
            expected = 1.0 - (1.0 - rate) ** runs - rate**runs
            assert power.flip_probability(rate, runs) == pytest.approx(expected)


def test_the_default_budget_has_the_power_the_reports_quote():
    """The numbers in the report, the README and the paper, all from one place."""
    assert power.flip_probability(0.5, 7) == pytest.approx(0.9844, abs=5e-5)
    assert power.flip_probability(0.1, 7) == pytest.approx(0.5217, abs=5e-5)
    assert power.flip_probability(0.02, 7) == pytest.approx(0.1319, abs=5e-5)
    assert power.flip_probability(0.01, 7) == pytest.approx(0.0679, abs=5e-5)


def test_the_two_criteria_agree_for_a_rare_flake_and_part_for_a_frequent_one():
    # They differ by exactly rate**runs, which is negligible when the rate is small.
    for rate in (0.1, 0.05, 0.02, 0.01):
        assert power.observed_probability(rate, 7) == pytest.approx(
            power.flip_probability(rate, 7), abs=1e-6
        )
    # At a coin flip they are a factor of two apart in the complement: 0.8% of arms see
    # no failure at all and another 0.8% see nothing but failures.
    never = 1.0 - power.observed_probability(0.5, 7)
    no_flip = 1.0 - power.flip_probability(0.5, 7)
    assert never == pytest.approx(0.5**7, abs=1e-9)
    assert no_flip == pytest.approx(2 * 0.5**7, abs=1e-9)
    assert no_flip > never


def test_a_single_run_can_flip_nothing():
    """A budget sweep that starts at one run is not measuring detection there."""
    assert power.flip_probability(0.5, 1) == 0.0
    assert power.flip_probability(0.01, 1) == 0.0
    # But a single run can still *notice* a failure, which is the other criterion.
    assert power.observed_probability(0.5, 1) == pytest.approx(0.5)


def test_a_rate_of_zero_or_one_never_flips_at_any_budget():
    for runs in (2, 7, 1000):
        assert power.flip_probability(0.0, runs) == pytest.approx(0.0)
        assert power.flip_probability(1.0, runs) == pytest.approx(0.0)
    assert power.runs_for_power(0.0) is None
    assert power.runs_for_power(1.0) is None


def test_an_impossible_rate_or_budget_is_refused():
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        power.flip_probability(1.5, 7)
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        power.observed_probability(-0.1, 7)
    with pytest.raises(ValueError, match="at least one run"):
        power.flip_probability(0.5, 0)


# ------------------------------------------------------------------- budgets and bounds


def test_runs_for_power_is_the_smallest_budget_that_reaches_it():
    for rate in (0.5, 0.25, 0.1, 0.05, 0.02, 0.01):
        needed = power.runs_for_power(rate)
        assert needed is not None
        assert power.flip_probability(rate, needed) >= 0.95
        # Smallest: one fewer must fall short, which is what makes it a bound.
        assert power.flip_probability(rate, needed - 1) < 0.95


def test_the_budgets_the_paper_quotes():
    assert power.runs_for_power(0.1) == 29
    assert power.runs_for_power(0.02) == 149
    assert power.runs_for_power(0.01) == 299


def test_detectable_rate_is_the_inverse_of_runs_for_power():
    for runs in (7, 11, 29, 149):
        rate = power.detectable_rate(runs)
        assert rate is not None
        assert power.flip_probability(rate, runs) == pytest.approx(0.95, abs=1e-3)


def test_a_budget_too_small_for_any_rate_says_so_rather_than_guessing():
    """Two runs per arm cannot reach 95% confidence against a coin-flip test."""
    assert power.flip_probability(0.5, 2) == pytest.approx(0.5)
    for runs in (1, 2, 3, 4, 5):
        assert power.detectable_rate(runs) is None, runs
        assert "cannot reach" in power.caveat(runs)
    assert power.detectable_rate(6) is not None


def test_the_caveat_names_the_rate_and_fits_the_report_width():
    text = power.caveat(7)
    assert "34.9%" in text
    for line in text.splitlines():
        assert len(line) <= 74, line


def test_the_power_table_carries_both_criteria_and_the_budget():
    rows = power.power_table()
    assert [r["rate"] for r in rows] == list(power.REFERENCE_RATES)
    for row in rows:
        assert row["observed_power_at_this_budget"] >= row["flip_power_at_this_budget"]
        assert row["never_failed_once"] == pytest.approx(
            1.0 - row["observed_power_at_this_budget"], abs=1e-9
        )
        assert row["runs_for_target_flip_power"] >= power.DEFAULT_RUNS or row["rate"] >= 0.5


def test_the_default_matches_the_cli():
    """The power statements and the flag they describe must not drift apart."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "flake_detective", "investigate", "--help"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, out.stderr
    assert f"default {power.DEFAULT_RUNS}" in out.stdout or f"({power.DEFAULT_RUNS})" in out.stdout


# ------------------------------------------------------------------------------- the SPRT


def test_the_boundaries_are_walds():
    s = power.Sprt(p0=0.001, p1=0.02, alpha=0.05, beta=0.05)
    assert s.accept_flaky == pytest.approx(math.log(0.95 / 0.05))
    assert s.accept_stable == pytest.approx(math.log(0.05 / 0.95))
    assert s.accept_stable < 0 < s.accept_flaky


def test_an_inverted_or_degenerate_test_is_refused():
    with pytest.raises(ValueError, match="p0 < p1"):
        power.Sprt(p0=0.2, p1=0.1)
    with pytest.raises(ValueError, match="p0 < p1"):
        power.Sprt(p0=0.1, p1=0.1)
    with pytest.raises(ValueError, match="alpha and beta"):
        power.Sprt(p0=0.01, p1=0.1, alpha=0.0)
    # Boundaries that cross: the test would accept both hypotheses at once.
    with pytest.raises(ValueError, match="boundaries invert"):
        power.Sprt(p0=0.01, p1=0.1, alpha=0.6, beta=0.6)


def test_the_log_ratio_is_the_sum_of_the_per_run_increments():
    s = power.Sprt(p0=0.001, p1=0.02)
    up = math.log(0.02 / 0.001)
    down = math.log(0.98 / 0.999)
    assert s.log_ratio(0, 0) == pytest.approx(0.0)
    assert s.log_ratio(1, 1) == pytest.approx(up)
    assert s.log_ratio(0, 1) == pytest.approx(down)
    assert s.log_ratio(2, 10) == pytest.approx(2 * up + 8 * down)


def test_more_failures_than_runs_is_refused():
    s = power.Sprt(p0=0.001, p1=0.02)
    with pytest.raises(ValueError, match="not possible"):
        s.log_ratio(3, 2)
    with pytest.raises(ValueError, match="not possible"):
        s.decide(-1, 5)


def test_the_decision_is_undecided_until_the_evidence_arrives():
    s = power.Sprt(p0=0.001, p1=0.02)
    assert s.decide(0, 0) == "undecided"
    assert s.decide(0, 10) == "undecided"
    # One failure clears the upper boundary when "stable" means one run in a thousand.
    assert s.decide(1, 1) == "flaky"
    # And a long clean streak clears the lower one.
    assert s.decide(0, s.runs_to_call_stable()) == "stable"
    assert s.decide(0, s.runs_to_call_stable() - 1) == "undecided"


def test_the_streak_lengths_are_the_boundaries_divided_by_the_steps():
    s = power.Sprt(p0=0.001, p1=0.02)
    assert s.runs_to_call_stable() == 154
    assert s.runs_to_call_flaky() == 1
    assert s.decide(s.runs_to_call_flaky(), s.runs_to_call_flaky()) == "flaky"


def test_expected_runs_refuses_the_indifference_region_rather_than_dividing_by_zero():
    """The bug this module's docstring records: 1.0 run between 164 and 64."""
    s = power.Sprt(p0=0.001, p1=0.02)
    assert s.p0 < s.indifference_rate < s.p1
    assert s.expected_runs(s.indifference_rate) is None
    assert s.expected_runs(0.005) is None
    # Either side of it the approximation is defined, and both are large.
    assert s.expected_runs(0.001) > 100
    assert s.expected_runs(0.02) > 10


def test_expected_runs_is_exact_where_the_count_is_not_random():
    """At a rate of 0 or 1 every run goes the same way, so there is nothing to approximate."""
    s = power.Sprt(p0=0.001, p1=0.02)
    assert s.expected_runs(0.0) == float(s.runs_to_call_stable())
    assert s.expected_runs(1.0) == float(s.runs_to_call_flaky())


def test_expected_runs_falls_as_the_flake_gets_more_frequent():
    s = power.Sprt(p0=0.001, p1=0.02)
    defined = [(r, s.expected_runs(r)) for r in (0.02, 0.05, 0.1, 0.25, 0.5, 1.0)]
    values = [v for _, v in defined]
    assert all(v is not None for v in values)
    assert values == sorted(values, reverse=True), defined
    assert values[-1] >= 1.0


def test_the_worst_case_is_worse_than_either_side_of_it():
    """The number that says what the test really costs."""
    s = power.Sprt(p0=0.001, p1=0.02)
    worst = s.worst_case_runs()
    assert worst > 0
    assert worst > s.expected_runs(0.02)
    # Inside the region the drift vanishes, so the walk has no trend and the cost is the
    # driftless-barrier result rather than anything `expected_runs` could report.
    assert s.expected_runs(s.indifference_rate) is None
    assert worst == pytest.approx(150.7, abs=0.5)


def test_a_tighter_error_budget_costs_more_runs():
    loose = power.Sprt(p0=0.001, p1=0.02, alpha=0.1, beta=0.1)
    tight = power.Sprt(p0=0.001, p1=0.02, alpha=0.01, beta=0.01)
    assert tight.runs_to_call_stable() > loose.runs_to_call_stable()
    assert tight.expected_runs(0.0) > loose.expected_runs(0.0)


# ------------------------------------------------- the published power numbers

import json as _json  # noqa: E402
import pathlib as _pathlib  # noqa: E402

ROOT = _pathlib.Path(__file__).resolve().parents[1]
MEASURED = ROOT / "research" / "results" / "power.json"
RATES = ROOT / "docs" / "fixture-rates.json"
RESULTS_DOC = ROOT / "docs" / "RESULTS.md"
README = ROOT / "README.md"


def _published():
    if not MEASURED.exists():
        pytest.skip("research/results/power.json is not present")
    return _json.loads(MEASURED.read_text(encoding="utf-8"))


def test_the_fixtures_flakes_really_do_fail_about_half_the_time():
    """The claim the whole section rests on, measured rather than read off the source."""
    fixture = _published()["fixture"]
    assert fixture["available"]
    assert fixture["runs_per_arm"] >= 100, "a rate this precise needs a long run"
    rates = {row["cause"]: row["rate"] for row in fixture["flakes"].values()}
    assert rates["clock"] == pytest.approx(0.433, abs=5e-4)
    assert rates["hash-seed"] == pytest.approx(0.525, abs=5e-4)
    assert rates["nondeterminism"] == pytest.approx(0.533, abs=5e-4)
    assert _published()["fixture_rate_mean"] == pytest.approx(0.497, abs=5e-4)
    # Every one of them is near a coin flip, which is the easiest case there is.
    for cause, rate in rates.items():
        assert 0.3 <= rate <= 0.7, (cause, rate)


def test_a_rate_is_read_from_the_arm_the_flake_responds_to():
    """A hash-seed flake's baseline rate is zero; reading it there would say 0%."""
    for row in _published()["fixture"]["flakes"].values():
        assert row["rate"] > 0.0, row
        assert row["runs_in_that_arm"] >= 100, row
    arms = {row["arm_it_responds_to"] for row in _published()["fixture"]["flakes"].values()}
    # The nondeterministic one responds to the baseline; the others do not.
    assert "baseline" in arms and len(arms) > 1, arms


def test_two_runs_per_arm_reach_confidence_against_nothing():
    """What the published sweep's detection of 1.0 at two runs is not evidence for."""
    by_budget = _published()["by_budget"]
    assert by_budget["2"]["detectable_rate_at_95"] is None
    assert by_budget["2"]["power_at_the_fixtures_rates"] == pytest.approx(0.496, abs=5e-4)
    assert by_budget["2"]["power_at_2_percent"] == pytest.approx(0.039, abs=5e-4)
    sweep = _published()["sweep"]
    if sweep.get("available"):
        assert sweep["smallest_budget_with_detection_1"] == 2
        assert sweep["samples_per_budget"] == 1


def test_the_default_budget_row_is_the_one_both_documents_quote():
    row = _published()["by_budget"][str(power.DEFAULT_RUNS)]
    assert row["detectable_rate_at_95"] == pytest.approx(0.349, abs=5e-4)
    assert row["power_at_the_fixtures_rates"] == pytest.approx(0.982, abs=5e-4)
    assert row["power_at_2_percent"] == pytest.approx(0.132, abs=5e-4)
    for doc in (RESULTS_DOC, README):
        text = doc.read_text(encoding="utf-8")
        assert "34.9%" in text, doc.name
        assert "86.8%" in text, doc.name
        assert "149" in text, doc.name


def test_power_rises_with_the_budget_at_every_rate():
    """Monotonicity, over the published table rather than over fresh arithmetic."""
    by_budget = _published()["by_budget"]
    order = sorted(by_budget, key=int)
    for key in ("power_at_the_fixtures_rates", "power_at_10_percent", "power_at_2_percent"):
        values = [by_budget[b][key] for b in order]
        assert values == sorted(values), (key, values)


def test_the_documents_no_longer_claim_a_few_runs_generalise():
    """The sweep's curve is about attribution; an edit must not quietly widen it back."""
    text = RESULTS_DOC.read_text(encoding="utf-8")
    assert "measures **attribution**" in text
    assert "not the power of a budget" in text
    assert "reach 95% confidence against no flake rate at all" in text
    assert "whose flaky\ntests fail 30–70% of the time" not in text
