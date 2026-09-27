"""Ctrl-C: kill the children, keep what finished, say "interrupted", exit 130, no traceback.

It used to print a twenty-line subprocess/threading traceback and throw away every run
that had completed. These tests stand in for the keypress by raising KeyboardInterrupt
from inside a run, which is where it lands in the main thread in real use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flake_detective import run as run_mod
from flake_detective.cli import main
from flake_detective.run import Observed
from flake_detective.types import Arm


def _fake_runs(monkeypatch, interrupt_on: int, seconds: float = 0.0):
    """Replace real pytest runs: each passes except call `interrupt_on`, which is Ctrl-C."""
    import time

    calls = {"n": 0}

    def fake(**kw):
        calls["n"] += 1
        if calls["n"] == interrupt_on:
            raise KeyboardInterrupt
        time.sleep(seconds)
        return Observed({"t.py::a": "passed", "t.py::b": "failed"}), ""

    monkeypatch.setattr(run_mod, "run_observed", fake)
    return calls


@pytest.mark.parametrize("jobs", [1, 3])
def test_an_interrupted_arm_keeps_the_runs_that_finished(monkeypatch, jobs):
    if jobs > 1:
        # Worker threads never see KeyboardInterrupt; the main thread does, while it
        # waits. Simulate that by interrupting the wait itself.
        real_wait = run_mod.wait
        state = {"n": 0}

        def wait(*a, **k):
            state["n"] += 1
            if state["n"] == 2:
                raise KeyboardInterrupt
            return real_wait(*a, **k)

        monkeypatch.setattr(run_mod, "wait", wait)
        _fake_runs(monkeypatch, interrupt_on=10_000, seconds=0.4)
    else:
        _fake_runs(monkeypatch, interrupt_on=3)
    run_mod.reset()
    try:
        arm = run_mod.execute(Arm("baseline", "b"), [[{}] for _ in range(6)], jobs=jobs)
    finally:
        run_mod.reset()
    assert arm.interrupted
    assert 1 <= arm.runs < 6
    assert arm.attempted == arm.runs
    assert arm.failures["t.py::b"] == arm.runs


def test_ctrl_c_prints_partial_results_and_exits_130_without_a_traceback(
    tmp_path: Path, monkeypatch, capsys
):
    (tmp_path / "test_x.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    # Collection is real; the first run of the second arm is the keypress.
    _fake_runs(monkeypatch, interrupt_on=4)
    code = main(["investigate", str(tmp_path), "--runs", "3", "--quiet", "--seed", "9"])
    cap = capsys.readouterr()
    assert code == 130
    assert "Traceback" not in cap.out + cap.err
    assert "INTERRUPTED" in cap.out
    assert "baseline   3 runs" in cap.out  # the finished arm is kept
    assert "interrupted" in cap.err
    assert "order seed 9" in cap.out


def test_ctrl_c_before_anything_finished_says_so(tmp_path: Path, monkeypatch, capsys):
    (tmp_path / "test_x.py").write_text("def test_a():\n    pass\n", encoding="utf-8")

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(run_mod, "collect_detailed", boom)
    code = main(["investigate", str(tmp_path), "--quiet"])
    cap = capsys.readouterr()
    assert code == 130
    assert "Traceback" not in cap.out + cap.err
    assert "interrupted" in cap.err.lower()


def test_stop_all_kills_a_running_child_and_refuses_new_ones(tmp_path: Path):
    import sys
    import threading
    import time

    run_mod.reset()
    result = {}

    def go():
        result["r"] = run_mod._spawn(
            [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, timeout=120
        )

    t = threading.Thread(target=go)
    started = time.monotonic()
    t.start()
    time.sleep(1.0)
    run_mod.stop_all()
    t.join(30)
    try:
        assert not t.is_alive()
        assert time.monotonic() - started < 30
        assert result["r"] == (None, "interrupted")
        assert run_mod._spawn([sys.executable, "-c", "pass"], tmp_path, 60) == (
            None,
            "interrupted",
        )
    finally:
        run_mod.reset()
