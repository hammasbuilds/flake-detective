"""The sweep path, which produced a published table and had no test.

`suite-auditor coverage`, run with this project's slow marker enabled, reports 81 of 92
functions reached. Four of the eleven it does not reach are the sweep: `sweep`,
`sweep_text`, `write_json` and `searched_causes`. The README publishes a table from that
code - "What a run count buys, eight sweeps on 2026-10-07 (`bench --sweep --json`)" -
so a published result came from the least-exercised path in the package.

`run` is substituted here. These tests are about the sweep's own arithmetic and
rendering: that it reports one row per run count, stops on an error without pretending
the remaining rows exist, and that the text and the JSON say the same thing as the rows.
Scoring the fixture for real is what `bench` already does, and it takes minutes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from flake_detective import bench  # noqa: E402


def result(detection: float, attribution: float, seconds: float = 3.0) -> dict:
    return {
        "detection": detection,
        "attribution": attribution,
        "false_positive_rate": 0.0,
        "seconds": seconds,
        "reported": {"order": "test_order_dependent.py::test_aaa_first_one_wins"},
        "runs": 7,
    }


@pytest.fixture
def stub_run(monkeypatch):
    """Replace the scorer; record the run counts it was asked for."""
    asked: list[int] = []

    def fake(runs, timeout, python, jobs, progress, arms):  # noqa: ANN001, ANN202
        asked.append(runs)
        # Detection improves with the run count, which is the shape the real sweep shows.
        return result(min(1.0, 0.5 + runs / 10), min(1.0, 0.4 + runs / 10))

    monkeypatch.setattr(bench, "run", fake)
    return asked


class TestSweep:
    def test_one_row_per_run_count_in_order(self, stub_run) -> None:
        out = bench.sweep(counts=(1, 3, 7))
        assert [r["runs_per_arm"] for r in out["sweep"]] == [1, 3, 7]
        assert stub_run == [1, 3, 7]
        assert "error" not in out

    def test_each_row_carries_the_scores_for_that_count(self, stub_run) -> None:
        out = bench.sweep(counts=(1, 7))
        first, last = out["sweep"]
        assert first["detection"] == pytest.approx(0.6)
        assert last["detection"] == pytest.approx(1.0)
        assert last["detection"] > first["detection"], (
            "the whole point of the table is that more runs find more"
        )

    def test_an_error_stops_the_sweep_and_is_reported(self, monkeypatch) -> None:
        """Half a curve must not be published as a curve.

        The rows measured before the failure are kept - they are real - but the error
        travels with them so no reader takes the table for a complete sweep.
        """
        calls = []

        def fake(runs, timeout, python, jobs, progress, arms):  # noqa: ANN001, ANN202
            calls.append(runs)
            if runs == 3:
                return {"error": "the target's suite could not be run"}
            return result(0.6, 0.5)

        monkeypatch.setattr(bench, "run", fake)
        out = bench.sweep(counts=(1, 3, 7))
        assert out["error"] == "the target's suite could not be run"
        assert [r["runs_per_arm"] for r in out["sweep"]] == [1]
        assert calls == [1, 3], "the sweep carried on past a failing run count"

    def test_progress_is_reported_per_count(self, stub_run) -> None:
        said: list[str] = []
        bench.sweep(counts=(1, 2), progress=said.append)
        assert any("1 runs per arm" in line for line in said)
        assert any("2 runs per arm" in line for line in said)

    def test_it_runs_without_a_progress_callback(self, stub_run) -> None:
        """The default is a no-op, and a missing callback must not be a crash."""
        assert bench.sweep(counts=(1,))["sweep"]


class TestSweepText:
    def test_every_row_appears(self, stub_run) -> None:
        out = bench.sweep(counts=(1, 3, 7))
        text = bench.sweep_text(out)
        for row in out["sweep"]:
            assert str(row["runs_per_arm"]) in text
        assert "RUNS PER ARM" in text

    def test_an_errored_sweep_renders_the_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            bench,
            "run",
            lambda runs, timeout, python, jobs, progress, arms: {"error": "no tests"},
        )
        text = bench.sweep_text(bench.sweep(counts=(1,)))
        assert "no tests" in text


class TestWriteJson:
    def test_it_writes_what_the_sweep_returned(self, tmp_path: Path, stub_run) -> None:
        out = bench.sweep(counts=(1, 3))
        path = tmp_path / "nested" / "sweep.json"
        bench.write_json(out, path)
        assert json.loads(path.read_text(encoding="utf-8")) == out

    def test_it_creates_the_directory(self, tmp_path: Path, stub_run) -> None:
        """`--json docs/sweep.json` on a fresh clone must not fail on a missing docs/."""
        path = tmp_path / "a" / "b" / "c.json"
        bench.write_json({"sweep": []}, path)
        assert path.is_file()


class TestSearchedCauses:
    class _Arm:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Inv:
        def __init__(self, *names: str) -> None:
            self.arms = [TestSearchedCauses._Arm(n) for n in names]

    def test_nondeterminism_is_always_searched(self) -> None:
        """It is not an arm - the baseline alone establishes it."""
        assert "nondeterminism" in bench.searched_causes(self._Inv("baseline"))

    def test_an_arm_that_ran_contributes_its_cause(self) -> None:
        from flake_detective.classify import ARM_CAUSE

        arm = next(iter(ARM_CAUSE))
        causes = bench.searched_causes(self._Inv("baseline", arm))
        assert ARM_CAUSE[arm].value in causes

    def test_the_correspondence_is_not_restated_here(self) -> None:
        """Every cause this reports must come from ARM_CAUSE or be nondeterminism.

        The docstring says restating the mapping is how the two would drift apart, so
        this checks the claim rather than trusting it.
        """
        from flake_detective.classify import ARM_CAUSE

        names = [n for n in ARM_CAUSE] + ["baseline"]
        causes = bench.searched_causes(self._Inv(*names))
        allowed = {c.value for c in ARM_CAUSE.values()} | {"nondeterminism"}
        assert causes <= allowed, f"{causes - allowed} comes from nowhere in ARM_CAUSE"

    def test_an_unknown_arm_name_contributes_nothing(self) -> None:
        causes = bench.searched_causes(self._Inv("baseline", "not_an_arm"))
        assert causes == {"nondeterminism"}
