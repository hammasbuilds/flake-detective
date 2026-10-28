"""The progress writer, the option parsers, and the xdist probe.

Four more of the functions `suite-auditor coverage` reports as reached by no test. None
needs a suite run:

  * `_Say` writes progress to stderr, overwriting a transient line on a terminal and
    printing one line per update elsewhere, so a CI log still shows the run count moving.
    Both branches are reachable by setting whether stderr is a terminal.
  * `_positive_float` rejects a timeout a user typed wrongly. An option parser that
    accepts `--timeout 0` or `--timeout abc` and fails later, deep in a run, is worse
    than one that refuses it at the boundary.
  * `xdist_available` decides whether the parallel arm can run at all. Its docstring
    says why it is checked rather than assumed: without xdist, `-n` is unrecognised,
    pytest exits 4, and every run in the arm scores as unscoreable - which reads in the
    report as an arm that found nothing rather than an arm that never ran. That is a
    wrong answer about the target, so the probe has to be right.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from flake_detective import cli, run as run_module  # noqa: E402


class TestSay:
    def test_on_a_terminal_a_transient_line_is_overwritten(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
        say = cli._Say()
        say("run 1 of 7", transient=True)
        say("run 2 of 7", transient=True)
        err = capsys.readouterr().err
        assert err.count("\r") == 2, "a transient line must return to the start"
        assert "\n" not in err.rstrip("\n"), "transient lines must not each take a line"

    def test_off_a_terminal_every_line_is_printed(self, capsys, monkeypatch) -> None:
        """A CI log has no cursor to move, and must still show progress."""
        monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)
        say = cli._Say()
        say("run 1 of 7", transient=True)
        say("run 2 of 7", transient=True)
        lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.strip()]
        assert len(lines) == 2


class TestSayContinued:
    def test_a_permanent_line_clears_a_pending_transient_one(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
        say = cli._Say()
        say("working", transient=True)
        say("finished")
        err = capsys.readouterr().err
        assert err.endswith("finished\n")
        assert say.pending == 0

    def test_done_closes_a_pending_line(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
        say = cli._Say()
        say("working", transient=True)
        assert say.pending
        say.done()
        assert say.pending == 0
        assert capsys.readouterr().err.endswith("\n")

    def test_done_on_a_clean_writer_prints_nothing(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
        say = cli._Say()
        say.done()
        assert capsys.readouterr().err == ""

    def test_a_shorter_transient_line_blanks_the_longer_one(self, capsys, monkeypatch) -> None:
        """Otherwise the tail of the previous line is left on screen."""
        monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
        say = cli._Say()
        say("a very long progress message", transient=True)
        say("short", transient=True)
        err = capsys.readouterr().err
        assert err.rstrip().endswith(" ") or "short " in err


class TestPositiveFloat:
    def test_a_normal_value(self) -> None:
        assert cli._positive_float("12.5") == 12.5

    @pytest.mark.parametrize("bad", ["0", "-1", "-0.5"])
    def test_zero_and_below_are_refused(self, bad: str) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="more than 0"):
            cli._positive_float(bad)

    @pytest.mark.parametrize("bad", ["abc", "", "12s", "1,5"])
    def test_something_that_is_not_a_number_is_refused_with_its_own_message(
        self, bad: str
    ) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="number of seconds"):
            cli._positive_float(bad)


class TestXdistAvailable:
    def test_true_when_the_import_succeeds(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            run_module.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""),
        )
        assert run_module.xdist_available(tmp_path) is True

    def test_false_when_the_import_fails(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            run_module.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", "ModuleNotFoundError"),
        )
        assert run_module.xdist_available(tmp_path) is False

    def test_false_rather_than_raising_when_the_probe_cannot_run(
        self, tmp_path, monkeypatch
    ) -> None:
        """A missing interpreter must not end the investigation."""
        def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
            raise OSError("no such interpreter")

        monkeypatch.setattr(run_module.subprocess, "run", boom)
        assert run_module.xdist_available(tmp_path) is False

    def test_it_probes_the_interpreter_it_was_given(self, tmp_path, monkeypatch) -> None:
        """The target's interpreter, not ours - xdist may be in one and not the other."""
        seen: list[list[str]] = []

        def record(cmd, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
            seen.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(run_module.subprocess, "run", record)
        run_module.xdist_available(tmp_path, python="D:/elsewhere/python.exe")
        assert seen[0][0] == "D:/elsewhere/python.exe"
        assert "import xdist" in " ".join(seen[0])
