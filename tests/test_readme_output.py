"""The README's "What it prints" block is what the documented command prints.

`flake-detective fixture ./fx` then `flake-detective investigate ./fx --seed 0`: with the
seed fixed, the order, hash-seed and clock rows and their verdicts are deterministic, so
they must match the README character for character. (The nondeterministic row and the
wall time vary by design and are not compared.) The block once carried an arm
description that read "failed 3 of 7 runs when the wall clock frozen at ..."; it is
generated from a real run now, and this keeps it that way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flake_detective import cli

README = Path(__file__).resolve().parent.parent / "README.md"

STABLE_ROWS = (
    "test_order_dependent.py::test_aaa_first_one_wins",
    "..._hash_dependent.py::test_first_of_a_set_is_stable",
    "test_clock_dependent.py::test_second_is_even",
)


def _rows(text: str) -> list[str]:
    """Each deterministic row, its verdict and its fix line."""
    lines = text.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        if line.startswith(STABLE_ROWS):
            out.extend(x.rstrip() for x in lines[i : i + 3])
    return out


@pytest.mark.slow
@pytest.mark.skipif(not README.exists(), reason="README.md is not shipped with the tests")
def test_the_readme_block_is_what_the_command_prints(tmp_path, capsys):
    shown = README.read_text(encoding="utf-8").split("## What it prints", 1)[1]
    shown = shown.split("```", 2)[1]
    assert cli.main(["fixture", str(tmp_path / "fx")]) == 0
    capsys.readouterr()
    assert cli.main(["investigate", str(tmp_path / "fx"), "--seed", "0", "--quiet"]) == 0
    printed = capsys.readouterr().out
    assert len(_rows(printed)) == 9
    assert _rows(printed) == _rows(shown)
    # the header lines, minus the wall time
    for line in shown.splitlines():
        if line.startswith(("  baseline ", "  order ", "  hashseed ", "  clock ")):
            assert line in printed.splitlines(), line
