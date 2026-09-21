"""Running a real suite: collection, scoring, and the conditions actually being pinned."""

from __future__ import annotations

from pathlib import Path

import pytest

from flake_detective import freeze
from flake_detective.run import collect, run_once


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    d = tmp_path / "suite"
    d.mkdir()
    (d / "test_x.py").write_text(
        "def test_passes():\n    assert True\n\ndef test_fails():\n    assert False\n",
        encoding="utf-8",
    )
    return d


def test_collect_finds_every_test(suite: Path):
    ids = collect(suite)
    assert sorted(ids) == ["test_x.py::test_fails", "test_x.py::test_passes"]


def test_collect_on_an_empty_directory_is_empty(tmp_path: Path):
    assert collect(tmp_path) == []


def test_run_once_returns_only_the_failures(suite: Path):
    assert run_once(suite) == {"test_x.py::test_fails"}


def test_a_suite_with_no_tests_is_unscoreable_not_perfect(tmp_path: Path):
    """pytest exits 5 for "nothing collected". Reading that as zero failures would report
    a clean bill of health for a directory that was never examined."""
    assert run_once(tmp_path) is None


def test_a_missing_path_is_unscoreable(tmp_path: Path):
    assert run_once(tmp_path / "nope") is None


def test_the_order_given_is_the_order_run(tmp_path: Path):
    """The order arm is worthless if pytest re-sorts what it is handed."""
    d = tmp_path / "s"
    d.mkdir()
    (d / "test_o.py").write_text(
        "_SEEN = []\n"
        "\n"
        "def test_a():\n"
        "    _SEEN.append('a')\n"
        "    assert _SEEN == ['a']\n"
        "\n"
        "def test_b():\n"
        "    _SEEN.append('b')\n"
        "    assert _SEEN == ['b']\n",
        encoding="utf-8",
    )
    forward = ["test_o.py::test_a", "test_o.py::test_b"]
    assert run_once(d, order=forward) == {"test_o.py::test_b"}
    assert run_once(d, order=list(reversed(forward))) == {"test_o.py::test_a"}


def test_hashseed_reaches_the_subprocess(tmp_path: Path):
    """If it did not, the hashseed arm would be a silent duplicate of the baseline."""
    d = tmp_path / "s"
    d.mkdir()
    (d / "test_h.py").write_text(
        "import os\n\ndef test_seed():\n    assert os.environ['PYTHONHASHSEED'] == '7'\n",
        encoding="utf-8",
    )
    assert run_once(d, hashseed=7) == set()
    assert run_once(d, hashseed=0) == {"test_h.py::test_seed"}


def test_the_freeze_reaches_the_subprocess(tmp_path: Path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "test_c.py").write_text(
        "import datetime\n\ndef test_year():\n    assert datetime.date.today().year == 2024\n",
        encoding="utf-8",
    )
    assert run_once(d, epoch=freeze.BASELINE_EPOCH) == set()
    assert run_once(d, epoch=freeze.CLOCK_EPOCHS[5]) == {"test_c.py::test_year"}


def test_a_collection_error_is_unscoreable_not_a_failure(tmp_path: Path):
    """A suite that will not import has not told you anything about its tests."""
    d = tmp_path / "s"
    d.mkdir()
    (d / "test_broken.py").write_text("import a_module_that_does_not_exist\n", encoding="utf-8")
    # pytest exits 2 on a collection error; treating that as "every test failed" would
    # flood the report with tests that were never run.
    assert run_once(d) is None
