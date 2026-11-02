"""The three arms no test reaches: timezone, locale and parallel.

With the slow marker enabled, `suite-auditor coverage` reports 89 of 92 functions reached
and these are the three that remain. Two are the arms the bench reports as "not scoreable
here" on Windows, and the third needs pytest-xdist - so the suite never runs them, and
the code that builds their plans is unexercised on this machine even though it ships and
runs on other people's.

What is checked is the plan each arm builds, with `execute` substituted, because that is
where the arm's meaning lives. The timezone arm must vary the timezone and hold the
instant; the clock arm does the opposite; varying both at once would leave nothing to
attribute a flip to, which is the distinction the timezone arm's docstring exists to
draw. A wrong plan here produces a confident wrong cause in the report.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from flake_detective import run as run_module  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    """Capture the plan each arm hands to `execute`, instead of running it."""
    seen: dict = {}

    def fake_execute(arm, plan, jobs, tick, stop_when=None):  # noqa: ANN001, ANN202
        seen["arm"] = arm
        seen["plan"] = list(plan)
        seen["jobs"] = jobs
        # Recorded, not ignored: every arm has to forward the adaptive stopping rule, and
        # three of the eight forward it through a signature where `rootdir` sits between
        # `tick` and it. An arm that dropped it would silently run the fixed budget while
        # the report printed a sequential one.
        seen.setdefault("stop_when", {})[arm.name] = stop_when
        return arm

    monkeypatch.setattr(run_module, "execute", fake_execute)
    return seen


def steps(plan) -> list[dict]:  # noqa: ANN001
    """The call description for each planned run.

    A plan is a list of groups and each group holds the keyword dict `_call` was built
    from, so the arm's choices are readable without running anything. An earlier version
    of this file guessed at `functools.partial` keywords, found `None` everywhere, and
    reported four failures against correct code.
    """
    out = []
    for group in plan:
        assert group, "an empty group in the plan"
        out.append(group[0])
    return out


class TestTimezoneArm:
    def test_it_plans_one_call_per_run(self, tmp_path, captured) -> None:
        run_module.timezone_arm(tmp_path, "tests", runs=4, timeout=10, epoch=1000.0)
        assert len(captured["plan"]) == 4

    def test_the_timezone_varies_across_the_runs(self, tmp_path, captured) -> None:
        run_module.timezone_arm(tmp_path, "tests", runs=4, timeout=10, epoch=1000.0)
        zones = [s["extra_env"]["TZ"] for s in steps(captured["plan"])]
        assert len(set(zones)) == 4, f"the timezone must differ every run: {zones}"

    def test_the_instant_is_held_while_the_timezone_moves(self, tmp_path, captured) -> None:
        """The distinction from the clock arm, and the reason they are separate.

        The clock arm moves the date; this one keeps the instant and moves where you are
        standing, which is what breaks a naive datetime. Varying both would leave nothing
        to attribute a flip to.
        """
        run_module.timezone_arm(tmp_path, "tests", runs=3, timeout=10, epoch=1234.0)
        rows = steps(captured["plan"])
        assert {s["epoch"] for s in rows} == {1234.0}, "the instant moved too"
        assert {s["hashseed"] for s in rows} == {0}, "the hash seed moved too"

    def test_the_zones_are_far_apart_rather_than_neighbours(self, tmp_path, captured) -> None:
        """A one-hour shift breaks far less than a date-line one."""
        run_module.timezone_arm(tmp_path, "tests", runs=2, timeout=10, epoch=None)
        zones = [s["extra_env"]["TZ"] for s in steps(captured["plan"])]
        assert "UTC" in zones
        assert any(z != "UTC" for z in zones)

    def test_the_arm_is_named_and_described(self, tmp_path, captured) -> None:
        run_module.timezone_arm(tmp_path, "tests", runs=1, timeout=10, epoch=None)
        assert captured["arm"].name == "timezone"
        assert "timezone" in captured["arm"].description


class TestLocaleArm:
    def test_it_plans_one_call_per_run(self, tmp_path, captured) -> None:
        run_module.locale_arm(tmp_path, "tests", runs=3, timeout=10, epoch=None)
        assert len(captured["plan"]) == 3

    def test_the_locale_varies_across_the_runs(self, tmp_path, captured) -> None:
        run_module.locale_arm(tmp_path, "tests", runs=3, timeout=10, epoch=None)
        values = [s["extra_env"]["LC_ALL"] for s in steps(captured["plan"])]
        assert len(set(values)) == 3, f"the locale must differ every run: {values}"

    def test_lang_and_lc_all_are_set_together(self, tmp_path, captured) -> None:
        """LC_ALL alone leaves LANG disagreeing with it, which some libraries read."""
        run_module.locale_arm(tmp_path, "tests", runs=2, timeout=10, epoch=None)
        for s in steps(captured["plan"]):
            assert s["extra_env"]["LANG"] == s["extra_env"]["LC_ALL"]

    def test_nothing_else_varies(self, tmp_path, captured) -> None:
        run_module.locale_arm(tmp_path, "tests", runs=3, timeout=10, epoch=7.0)
        rows = steps(captured["plan"])
        assert {s["epoch"] for s in rows} == {7.0}
        assert {s["hashseed"] for s in rows} == {0}

    def test_the_arm_is_named(self, tmp_path, captured) -> None:
        run_module.locale_arm(tmp_path, "tests", runs=1, timeout=10, epoch=None)
        assert captured["arm"].name == "locale"


class TestParallelArm:
    def test_it_plans_one_call_per_run(self, tmp_path, captured) -> None:
        run_module.parallel_arm(tmp_path, "tests", runs=3, timeout=10, epoch=None)
        assert len(captured["plan"]) == 3

    def test_every_run_asks_pytest_for_workers(self, tmp_path, captured) -> None:
        """Without `-n` this arm is the baseline run under a different name."""
        run_module.parallel_arm(tmp_path, "tests", runs=2, timeout=10, epoch=None)
        for s in steps(captured["plan"]):
            args = s["extra_args"]
            assert "-n" in args, f"no worker count in {args}"
            assert int(args[args.index("-n") + 1]) > 1

    def test_the_runs_are_deliberately_identical(self, tmp_path, captured) -> None:
        """This arm varies nothing, and that is the design rather than an omission.

        The other arms change a variable and look for a flip. Here the variable is
        xdist's own scheduling, which differs between runs of the same command, so the
        plan holds the same call repeated and the flip comes from the scheduler. Written
        down because an identical plan reads like a bug until you know why.
        """
        run_module.parallel_arm(tmp_path, "tests", runs=3, timeout=10, epoch=None)
        rows = steps(captured["plan"])
        assert all(r == rows[0] for r in rows[1:])

    def test_the_arm_is_named(self, tmp_path, captured) -> None:
        run_module.parallel_arm(tmp_path, "tests", runs=1, timeout=10, epoch=None)
        assert captured["arm"].name == "parallel"
