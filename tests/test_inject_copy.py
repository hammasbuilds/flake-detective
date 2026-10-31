"""The scorer's repository copy must not follow a Windows junction.

`scripts/inject_and_score.py` plants a flake in a copy of the target rather than in the
target itself, and the copy used `shutil.copytree(symlinks=True)` with a list of junk
directory names. `symlinks=True` reads as covering every kind of link; it does not cover
junctions, because `os.path.islink()` returns False for one, so copytree walks through
and copies the target.

This script is pointed at local checkouts - it is how the twelve-suite figure in the
README was measured - and those checkouts keep their data in a junction. One points at
13.8 GB, and each arm makes its own copy, so the four arms would copy it four times. The
same defect in suite-auditor's scratch copy turned a 122-second audit into fifteen
minutes of silence with the budget unspent.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

# Imported under a guard, not bare. CI's second job copies `tests/` alone next to an
# installed distribution, where this directory does not exist; a bare import raises
# ModuleNotFoundError during collection, which fails the whole job instead of
# skipping this file.

inject_and_score = pytest.importorskip(
    "inject_and_score", reason="scripts/ and research/ are not shipped with the tests"
)
_ignore = inject_and_score._ignore
is_junction = inject_and_score.is_junction


def make_junction(link: Path, target: Path) -> bool:
    """A directory junction, or False where this platform cannot make one.

    `mklink /J` needs no administrator rights, unlike a symlink, which is why these
    checkouts use junctions for their data directories.
    """
    if sys.platform != "win32":
        return False
    out = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    return out.returncode == 0 and link.exists()


@pytest.fixture
def repo_with_a_junction(tmp_path: Path) -> Path:
    outside = tmp_path / "outside" / "deep"
    outside.mkdir(parents=True)
    for i in range(4):
        (outside / f"big{i}.bin").write_bytes(b"x" * 8192)

    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    if not make_junction(repo / "data", outside.parent):
        pytest.skip("this platform cannot create a directory junction")
    return repo


def test_a_junction_is_recognised(repo_with_a_junction: Path) -> None:
    """os.path.islink() does not answer this question, which is the whole problem."""
    import os

    repo = repo_with_a_junction
    assert is_junction(repo / "data")
    assert not os.path.islink(repo / "data")
    assert not is_junction(repo / "tests")
    assert not is_junction(repo / "nothing_here")


def test_the_copy_leaves_the_junction_out(repo_with_a_junction: Path) -> None:
    repo = repo_with_a_junction
    dest = Path(tempfile.mkdtemp(prefix="t_")) / repo.name
    try:
        shutil.copytree(repo, dest, symlinks=True, ignore=_ignore)
        assert (dest / "tests" / "test_ok.py").is_file(), "the repository itself must copy"
        assert not (dest / "data").exists()
        assert not any(f.name.startswith("big") for f in dest.rglob("*"))
    finally:
        shutil.rmtree(dest.parent, ignore_errors=True)


def test_the_junk_directories_are_still_skipped(repo_with_a_junction: Path) -> None:
    """The junction filter replaced ignore_patterns; it must not have dropped its job."""
    repo = repo_with_a_junction
    assert _ignore(str(repo), [".git", "tests", "data", "__pycache__"]) == {
        ".git",
        "data",
        "__pycache__",
    }
