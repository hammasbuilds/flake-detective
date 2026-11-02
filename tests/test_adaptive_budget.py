"""The sequential budget: the stopping rule, the plumbing, and what the report must say.

`power.py` had computed the cost of a fixed budget for a while before anything spent runs
by it, so the tool proved its own default weak and kept it. These tests cover the three
places that wiring can fail silently:

  * the **rule**. `all_decided` must be false on no evidence. An arm whose first run could
    not be scored would otherwise stop at once, having looked at nothing, and every test in
    the suite would be "stable" by vacuous truth.
  * the **plumbing**. Each of the eight arm builders has to forward the rule to `execute`,
    and two of them take `rootdir` positionally after `tick` - the parameter was first
    inserted ahead of it, which silently bound `rootdir` to `stop_when` and would have run
    the fixed budget while the report described a sequential one.
  * the **report**. An adaptive run that found flakes used to print them and say nothing
    about the tests it left at the cap, because the budget block sat inside the branch for
    a clean report. A run that names five flakes and leaves fourteen tests undecided is
    making an unbounded claim about the fourteen.

`execute` is driven with a fake `run_observed` rather than real pytest calls, so the
stopping arithmetic is tested without minutes of subprocesses.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from flake_detective import power, report
from flake_detective import run as run_module
from flake_detective.detective import ALL_ARMS, Options
from flake_detective.types import Arm, Investigation

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _sprt() -> power.Sprt:
    return power.Sprt(power.DEFAULT_P0, power.DEFAULT_P1)


# --------------------------------------------------------------------------- the rule


def test_no_evidence_is_not_a_decision():
    sprt = _sprt()
    assert power.all_decided(sprt, {}, {}) is False
    assert power.verdicts(sprt, {}, {}) == {}
    # A test present with zero observations is not decided either.
    assert power.all_decided(sprt, {}, {"t": 0}) is False


def test_a_clean_test_is_decided_exactly_at_the_floor():
    sprt = _sprt()
    floor = sprt.runs_to_call_stable()
    assert power.all_decided(sprt, {}, {"t": floor - 1}) is False
    assert power.all_decided(sprt, {}, {"t": floor}) is True


def test_one_undecided_test_holds_the_whole_arm():
    sprt = _sprt()
    floor = sprt.runs_to_call_stable()
    assert power.all_decided(sprt, {}, {"a": floor, "b": floor}) is True
    # Three clean runs is not enough to accept a test as stable at these settings, so one
    # such test holds the arm however settled the rest of the suite is. A failure would
    # *not* do - at p0=0.001 against p1=0.02 a single failure already crosses the flaky
    # boundary, so a failing test is decided at once and is not the case to test with.
    assert power.all_decided(sprt, {}, {"a": floor, "b": 3}) is False
    assert power.verdicts(sprt, {}, {"a": floor, "b": 3}) == {"a": "stable", "b": "undecided"}


def test_an_impossible_tally_raises_rather_than_being_clamped():
    """More failures than observations means the arm's bookkeeping is broken."""
    with pytest.raises(ValueError):
        power.verdicts(_sprt(), {"t": 5}, {"t": 3})


def test_the_verdict_is_about_the_rate_and_not_about_flipping():
    """A test that fails every run has a rate above p1, so it is "flaky" here."""
    sprt = _sprt()
    assert power.verdicts(sprt, {"t": 9}, {"t": 9}) == {"t": "flaky"}


def test_the_shape_reports_a_cap_below_the_floor_rather_than_correcting_it():
    sprt = _sprt()
    floor = sprt.runs_to_call_stable()
    assert power.budget_shape(sprt, floor)["cap_below_the_stable_floor"] is False
    tight = power.budget_shape(sprt, floor - 1)
    assert tight["cap_below_the_stable_floor"] is True
    assert tight["cap"] == floor - 1, "the cap asked for, not a corrected one"


# ------------------------------------------------------------------------ the plumbing


@pytest.fixture
def fake_runs(monkeypatch):
    """Make every pytest call return a fixed observation, and count the calls."""
    calls = {"n": 0}

    def fake_observed(**_kw):
        calls["n"] += 1
        obs = run_module.Observed()
        obs.outcomes["test_a"] = "passed"
        return obs, ""

    monkeypatch.setattr(run_module, "run_observed", fake_observed)
    return calls


def test_execute_stops_as_soon_as_the_rule_says_so(fake_runs):
    arm = Arm("baseline", "identical", tracked=True)
    seen = []

    def stop(probe: Arm) -> bool:
        seen.append(probe.runs)
        return probe.runs >= 3

    plan = [run_module._call(ROOT, "", 10.0, "", hashseed=0, epoch=None) for _ in range(20)]
    out = run_module.execute(arm, plan, 1, None, stop)
    assert out.stopped_early is True
    assert fake_runs["n"] == 3, "runs were spent after the rule said to stop"
    assert seen == [1, 2, 3]
    # The runs it never started are not failures to score.
    assert out.attempted == 3
    assert out.unscored == 0


def test_execute_without_a_rule_spends_the_whole_plan(fake_runs):
    arm = Arm("baseline", "identical", tracked=True)
    plan = [run_module._call(ROOT, "", 10.0, "", hashseed=0, epoch=None) for _ in range(5)]
    out = run_module.execute(arm, plan, 1, None)
    assert out.stopped_early is False
    assert fake_runs["n"] == 5
    assert out.attempted == 5


def test_the_rule_is_never_asked_about_a_part_finished_run(monkeypatch):
    """An isolation pass is one pytest call per test; half of one is not a run."""
    calls = {"n": 0}

    def fake_observed(**_kw):
        calls["n"] += 1
        obs = run_module.Observed()
        obs.outcomes[f"test_{calls['n'] % 3}"] = "passed"
        return obs, ""

    monkeypatch.setattr(run_module, "run_observed", fake_observed)
    arm = Arm("isolation", "one test at a time", tracked=True)
    # Three runs, each of which is three calls.
    plan = [
        [
            dict(repo=ROOT, target=f"t{i}-{j}", timeout=10.0, python="", hashseed=0, epoch=None)
            for j in range(3)
        ]
        for i in range(3)
    ]
    asked = []
    run_module.execute(arm, plan, 1, None, lambda probe: asked.append(probe.runs) or False)
    assert asked == [1, 2, 3], asked


def test_every_arm_builder_forwards_the_rule(monkeypatch):
    """Two of them take `rootdir` positionally after `tick`, which is where this broke."""
    import inspect

    builders = [
        getattr(run_module, name)
        for name in dir(run_module)
        if name.endswith("_arm") and callable(getattr(run_module, name))
    ]
    assert len(builders) == 8, [b.__name__ for b in builders]
    for builder in builders:
        parameters = inspect.signature(builder).parameters
        assert "stop_when" in parameters, builder.__name__
        # Last, so that a positional call cannot land something else in it.
        assert list(parameters)[-1] == "stop_when", builder.__name__

    forwarded = {}

    def fake_execute(arm, plan, jobs, tick, stop_when=None):  # noqa: ANN001, ANN202
        forwarded[arm.name] = stop_when
        return arm

    monkeypatch.setattr(run_module, "execute", fake_execute)
    sentinel = object()
    run_module.baseline_arm(ROOT, "", 2, 10.0, None, "", 1, None, stop_when=sentinel)
    run_module.order_arm(ROOT, "", ["a", "b"], 2, 10.0, None, 1, "", 1, None, "", sentinel)
    assert forwarded["baseline"] is sentinel
    assert forwarded["order"] is sentinel


def test_the_options_turn_runs_into_a_minimum_and_max_runs_into_the_cap():
    opts = Options(until_decided=True, runs=7, max_runs=200)
    assert opts.p0 == power.DEFAULT_P0
    assert opts.p1 == power.DEFAULT_P1
    assert opts.max_runs > opts.runs
    # Off by default: this mode costs the floor on every clean suite.
    assert Options().until_decided is False


# -------------------------------------------------------------------------- the report


def _inv(budget: dict, undecided: list[str], flakes=()) -> Investigation:
    inv = Investigation(
        arms=[Arm("baseline", "identical conditions, repeated", runs=59, attempted=59)],
        total_tests=16,
    )
    inv.arms[0].stopped_early = True
    inv.budget = budget
    inv.undecided = list(undecided)
    inv.flakes = list(flakes)
    return inv


def _shape(cap: int = 70) -> dict:
    shape = power.budget_shape(_sprt(), cap)
    shape["minimum_runs"] = 7
    return shape


def test_a_clean_adaptive_report_claims_a_decision_rather_than_a_bound():
    text = report.text(_inv(_shape(), []))
    assert "Every test was decided" in text
    # The fixed-budget power table must not appear beside it: there the run count is a
    # setting and the bound follows from it, here the run count is an outcome.
    assert "what 59 runs per arm can miss" not in text
    assert "raise --runs to lower that bound" not in text


def test_the_undecided_tests_are_named_in_a_clean_report():
    text = report.text(_inv(_shape(40), ["t_one", "t_two"]))
    assert "2 of 16 tests reached the 40-run cap" in text
    assert "t_one" in text and "t_two" in text
    assert "says nothing about them" in text


def test_a_cap_below_the_floor_is_said_out_loud():
    text = report.text(_inv(_shape(40), ["t_one"]))
    assert "cap is below that floor" in text


def test_the_undecided_tests_are_named_when_flakes_were_found_too():
    """The branch this used to be hidden behind."""
    from flake_detective.types import Cause, Flake

    flake = Flake(test_id="test_x", cause=Cause.NONDETERMINISM, evidence="failed 3 of 7")
    text = report.text(_inv(_shape(40), ["t_one", "t_two"], flakes=[flake]))
    assert "reached the 40-run cap" in text
    assert "t_one" in text


def test_the_json_carries_the_rule_and_the_undecided_list():
    payload = report.as_json(_inv(_shape(), ["t_one"]))
    assert payload["budget"]["cap"] == 70
    assert payload["undecided"] == ["t_one"]
    assert payload["arms"][0]["stopped_early"] is True
    # A consumer reading `ok` alone would publish a clean result for the test it knows
    # least about, which is why both keys are present whenever a budget is.
    assert "ok" in payload
    assert json.dumps(payload)


def test_a_fixed_budget_report_carries_neither_key():
    inv = Investigation(arms=[Arm("baseline", "x", runs=7, attempted=7)], total_tests=3)
    payload = report.as_json(inv)
    assert "budget" not in payload
    assert "undecided" not in payload
    assert "raise --runs to lower that bound" in report.text(inv)


def test_the_cli_exposes_the_mode_and_refuses_an_impossible_rate():
    from flake_detective.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["investigate", ".", "--until-decided", "--p1", "0.05"])
    assert args.until_decided is True
    assert args.p1 == 0.05
    for bad in ("0", "1", "1.5", "-0.1", "x"):
        with pytest.raises(SystemExit):
            parser.parse_args(["investigate", ".", "--p0", bad])


def test_every_arm_can_be_asked_for_alongside_the_mode():
    """The mode is orthogonal to which arms run, and nothing should couple them."""
    from flake_detective.cli import build_parser

    args = build_parser().parse_args(
        ["investigate", ".", "--until-decided", "--arms", ",".join(ALL_ARMS)]
    )
    assert set(args.arms) == set(ALL_ARMS)
    assert args.until_decided is True
