"""The command line as a user meets it: --help, bad arguments, broken setups, exit codes.

Every failure here was found by using the installed tool, not by reading it:
`investigate --help` crashed on Python 3.11-3.13, and an empty directory, a missing
pytest or a nonexistent interpreter all printed a reassuring report and exited 0.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from flake_detective import __version__
from flake_detective.cli import build_parser, main

ROOT = Path(__file__).resolve().parents[1]


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


SUBCOMMANDS = sorted(_subparsers(build_parser()))


def test_the_subcommands_are_the_documented_ones():
    assert SUBCOMMANDS == ["bench", "fixture", "investigate"]


@pytest.mark.parametrize("argv", [[], *([c] for c in SUBCOMMANDS)], ids=lambda a: " ".join(a))
def test_help_works_for_every_subcommand(argv, capsys):
    with pytest.raises(SystemExit) as e:
        main([*argv, "--help"])
    assert e.value.code == 0
    assert "usage: flake-detective" in capsys.readouterr().out


def test_no_help_string_has_a_bare_percent_sign():
    """argparse %-formats help. A literal "3.1%" crashed --help with
    `TypeError: %o format: an integer is required, not dict` on 3.11-3.13 - and not on
    3.14, which is what the tests ran on, so the in-process --help test above cannot be
    the only guard. This one does not depend on the interpreter."""
    parsers = [build_parser(), *_subparsers(build_parser()).values()]
    for parser in parsers:
        for action in parser._actions:
            help_text = action.help or ""
            bare = re.findall(r"%(?!%|\(\w+\)s)", help_text.replace("%%", ""))
            assert not bare, f"{parser.prog} {action.dest}: bare % in {help_text!r}"
            # And it formats, the way argparse formats it.
            help_text % {**vars(action), "prog": parser.prog, "default": action.default}


def test_help_runs_in_a_fresh_interpreter():
    """What a user actually types, through the console-script entry point's module."""
    proc = subprocess.run(
        [sys.executable, "-m", "flake_detective.cli", "investigate", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "3.1%" in proc.stdout


def test_version_flag_matches_the_package_metadata(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"flake-detective {__version__}"
    # The installed metadata always exists; pyproject.toml only in a source checkout (the
    # installed-wheel CI job copies just tests/, and this read used to fail there).
    from importlib.metadata import version

    assert version("flake-detective") == __version__
    if (ROOT / "pyproject.toml").exists():
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert project["project"]["version"] == __version__


@pytest.mark.parametrize("runs", ["0", "-3", "seven"])
def test_runs_below_one_is_refused(runs, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["investigate", str(tmp_path), "--runs", runs])
    assert e.value.code == 2
    assert "--runs" in capsys.readouterr().err


def test_an_unknown_arm_is_refused_with_the_valid_list(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["investigate", str(tmp_path), "--arms", "order,bogus"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "bogus" in err
    for name in ("order", "hashseed", "clock", "isolation"):
        assert name in err


def test_arms_accepts_all_and_is_case_insensitive():
    from flake_detective.cli import _arms
    from flake_detective.detective import ALL_ARMS

    assert _arms("all") == ALL_ARMS
    assert _arms("Order, CLOCK") == ("order", "clock")


def test_a_nonexistent_interpreter_is_an_error_not_a_clean_report(tmp_path, capsys):
    code = main(["investigate", str(tmp_path), "--python", str(tmp_path / "nope" / "python")])
    assert code == 2
    assert "no such interpreter" in capsys.readouterr().err


def test_a_venv_directory_is_accepted_as_the_interpreter(tmp_path):
    from flake_detective.cli import _resolve_python

    venv = tmp_path / "venv"
    exe = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    got, err = _resolve_python(str(venv))
    assert err == ""
    assert Path(got) == exe.resolve()


def _err_only(capsys) -> str:
    """An error report goes to stderr, and nothing goes to stdout: a script capturing
    stdout must not mistake "nothing was examined" for findings."""
    cap = capsys.readouterr()
    assert cap.out.strip() == "", cap.out
    return cap.err


def test_a_file_as_repo_suggests_the_target_form(tmp_path, capsys):
    f = tmp_path / "test_x.py"
    f.write_text("def test_a():\n    pass\n", encoding="utf-8")
    assert main(["investigate", str(f)]) == 2
    err = capsys.readouterr().err
    assert "TARGET" in err and "test_x.py" in err


def test_an_empty_directory_exits_nonzero_and_says_why(tmp_path, capsys):
    code = main(["investigate", str(tmp_path), "--fail-on-flake", "--quiet"])
    out = _err_only(capsys)
    assert code == 2
    assert "NOTHING WAS EXAMINED" in out
    assert "no tests" in out
    assert "No flaky tests found" not in out


def test_a_collection_error_exits_nonzero_and_names_the_file(tmp_path, capsys):
    (tmp_path / "test_broken.py").write_text("import a_module_that_is_not_there\n", "utf-8")
    (tmp_path / "test_fine.py").write_text("def test_ok():\n    pass\n", "utf-8")
    code = main(["investigate", str(tmp_path), "--quiet", "--runs", "2"])
    out = _err_only(capsys)
    assert code == 2
    assert "errors while collecting" in out
    assert "test_broken.py" in out
    assert "No flaky tests found" not in out


def test_a_nonexistent_target_exits_nonzero(tmp_path, capsys):
    (tmp_path / "test_fine.py").write_text("def test_ok():\n    pass\n", "utf-8")
    code = main(["investigate", str(tmp_path), "tests/nothing_here.py", "--quiet"])
    assert code == 2
    assert "NOTHING WAS EXAMINED" in _err_only(capsys)


def test_a_baseline_that_never_scores_is_an_error(tmp_path, capsys):
    """Collection succeeds, every run falls over. That used to print "No flaky tests
    found across 0 runs per arm" and exit 0."""
    (tmp_path / "conftest.py").write_text(
        "import os\n"
        "def pytest_runtestloop(session):\n"
        "    if not session.config.option.collectonly:\n"
        "        os._exit(3)\n",
        "utf-8",
    )
    (tmp_path / "test_fine.py").write_text("def test_ok():\n    pass\n", "utf-8")
    code = main(["investigate", str(tmp_path), "--quiet", "--runs", "2", "--arms", "order"])
    out = _err_only(capsys)
    assert code == 2
    assert "none of the 2 baseline runs could be scored" in out
    assert "exited with status 3" in out


@pytest.fixture(scope="module")
def python_without_pytest(tmp_path_factory) -> Path:
    """A real interpreter that cannot import pytest: a bare venv."""
    venv = tmp_path_factory.mktemp("bare") / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=300
    )
    exe = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    probe = subprocess.run([str(exe), "-c", "import pytest"], capture_output=True, check=False)
    if probe.returncode == 0:
        pytest.skip("pytest is importable even from a bare venv here")
    return exe


def test_missing_pytest_fails_loudly_in_investigate(tmp_path, capsys, python_without_pytest):
    (tmp_path / "test_fine.py").write_text("def test_ok():\n    pass\n", "utf-8")
    code = main(["investigate", str(tmp_path), "--quiet", "--python", str(python_without_pytest)])
    out = _err_only(capsys)
    assert code == 2
    assert "pytest is not importable" in out
    assert "-m pip install pytest" in out
    assert "No flaky tests found" not in out


def test_missing_pytest_fails_loudly_in_bench(capsys, python_without_pytest):
    code = main(["bench", "--quiet", "--runs", "2", "--python", str(python_without_pytest)])
    out = _err_only(capsys)
    assert code == 2
    assert "BENCHMARK DID NOT RUN" in out
    assert "detection" not in out


@pytest.mark.slow
def test_fail_on_flake_exits_one_and_a_clean_run_exits_zero(tmp_path, capsys):
    (tmp_path / "test_seed.py").write_text(
        "import os\n\ndef test_seed():\n    assert os.environ['PYTHONHASHSEED'] == '0'\n",
        "utf-8",
    )
    args = ["investigate", str(tmp_path), "--quiet", "--runs", "2", "--arms", "hashseed"]
    assert main([*args, "--fail-on-flake"]) == 1
    assert "hash-seed" in capsys.readouterr().out
    assert main(args) == 0


def test_json_is_written_for_a_failed_investigation_too(tmp_path, capsys):
    import json

    out = tmp_path / "r.json"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["investigate", str(empty), "--quiet", "--json", str(out)]) == 2
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["ok"] is False
    assert "no tests" in got["problem"]


def test_a_file_as_repo_suggests_the_project_not_the_files_own_folder(
    tmp_path, capsys, monkeypatch
):
    """`investigate stable/tests/test_ok.py` used to suggest
    `investigate stable/tests test_ok.py`, which moves pytest's rootdir, the conftest
    files it loads and what relative paths in the tests resolve against."""
    tests = tmp_path / "stable" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_ok.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert main(["investigate", str(Path("stable") / "tests" / "test_ok.py")]) == 2
    err = capsys.readouterr().err
    assert "investigate stable tests/test_ok.py" in err, err

    (tmp_path / "stable" / "pyproject.toml").write_text("[project]\nname='x'\n", "utf-8")
    deeper = tests / "unit"
    deeper.mkdir()
    (deeper / "test_u.py").write_text("def test_u():\n    pass\n", encoding="utf-8")
    assert main(["investigate", str(deeper / "test_u.py")]) == 2
    err = capsys.readouterr().err
    assert "investigate stable tests/unit/test_u.py" in err, err


@pytest.mark.slow
def test_the_seed_is_printed_and_can_be_given(tmp_path, capsys):
    (tmp_path / "test_o.py").write_text("def test_a():\n    pass\n", "utf-8")
    args = ["investigate", str(tmp_path), "--quiet", "--runs", "2", "--arms", "order"]
    assert main([*args, "--seed", "1234"]) == 0
    assert "order seed 1234 (--seed 1234 repeats these shuffles)" in capsys.readouterr().out
    assert main(args) == 0
    assert "order seed " in capsys.readouterr().out
