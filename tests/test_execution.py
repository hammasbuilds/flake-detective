"""How runs are executed: progress, concurrency, long orders, encodings, unscoreable arms."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from flake_detective import freeze
from flake_detective import run as run_mod
from flake_detective.detective import Options, investigate
from flake_detective.report import text
from flake_detective.types import Arm, Investigation

ORDER_PAIR = (
    "_SEEN = []\n"
    "\n"
    "def test_aaa():\n"
    "    _SEEN.append('a')\n"
    "    assert len(_SEEN) == 1\n"
    "\n"
    "def test_bbb():\n"
    "    _SEEN.append('b')\n"
    "\n"
    "def test_ccc():\n"
    "    pass\n"
)


@pytest.fixture
def pair(tmp_path: Path) -> Path:
    d = tmp_path / "s"
    d.mkdir()
    (d / "test_o.py").write_text(ORDER_PAIR, encoding="utf-8")
    return d


def test_a_long_order_goes_through_a_file_and_keeps_its_order(pair, monkeypatch):
    """Windows refuses a command line past 32,767 characters; a big shuffled suite hits
    that, and every order run would fail to start. Forced here with a limit of zero."""
    monkeypatch.setattr(run_mod, "MAX_ARGV_CHARS", 0)
    a, b = "test_o.py::test_aaa", "test_o.py::test_bbb"
    assert run_mod.run_once(pair, order=[b, a]) == {a}
    assert run_mod.run_once(pair, order=[a, b]) == set()


def test_the_order_file_runs_only_the_listed_tests(pair, monkeypatch):
    """Localisation and isolation pass a subset; the file path has to honour that too."""
    monkeypatch.setattr(run_mod, "MAX_ARGV_CHARS", 0)
    failed, why = run_mod.run_once_detailed(pair, order=["test_o.py::test_bbb"], extra_args=["-v"])
    assert failed == set(), why


def test_concurrent_runs_score_the_same_as_serial_ones(pair):
    tests = run_mod.collect(pair)
    serial = run_mod.order_arm(pair, "", tests, 6, 120, None, seed=3)
    parallel = run_mod.order_arm(pair, "", tests, 6, 120, None, seed=3, jobs=3)
    assert (serial.runs, serial.failures) == (parallel.runs, parallel.failures)
    assert serial.attempted == parallel.attempted == 6


def test_a_non_ascii_test_id_survives_the_round_trip(tmp_path: Path):
    """Collected ids are fed back as arguments and matched against failure lines, so
    both sides have to decode the same way. On Windows the child writes cp1252 unless
    told otherwise - and a character cp1252 lacks came back escaped as "\\u03bb",
    which as an argument names no test, so every run in the order arm exited 4."""
    d = tmp_path / "s"
    d.mkdir()
    # A function name, not a parametrize id: pytest escapes non-ASCII ids to \xdf, but
    # a node id built from an identifier or a path keeps the character itself.
    (d / "test_u.py").write_text(
        "def test_λ():\n    assert False\n\ndef test_naïve():\n    pass\n",
        encoding="utf-8",
    )
    ids = run_mod.collect(d)
    assert ids == ["test_u.py::test_λ", "test_u.py::test_naïve"], ids
    assert run_mod.run_once(d, order=list(reversed(ids))) == {"test_u.py::test_λ"}


def test_progress_ticks_once_per_run_and_counts_down(pair):
    seen: list[tuple[str, bool]] = []
    inv = investigate(
        pair,
        "",
        Options(runs=2, arms=("order",)),
        progress=lambda msg, transient=False: seen.append((msg, transient)),
    )
    assert inv.ok
    ticks = [m for m, t in seen if t]
    assert len(ticks) == 4  # baseline 2 + order 2
    assert ticks[0].startswith("baseline 1/2")
    assert "run 1 of 4" in ticks[0] and "left" in ticks[0]
    assert ticks[-1].startswith("order 2/2") and "run 4 of 4" in ticks[-1]


def test_an_arm_that_never_scores_is_reported_and_not_ok():
    baseline = Arm("baseline", "b", runs=3, attempted=3)
    order = Arm("order", "o", runs=0, attempted=3, error="pytest exited with status 4:\nboom")
    inv = Investigation(arms=[baseline, order], total_tests=2)
    assert inv.incomplete == ["order"]
    assert not inv.ok
    out = text(inv)
    assert "INCOMPLETE" in out and "boom" in out
    assert "No flaky tests found across" not in out


def test_partly_unscored_arms_say_how_many_runs_counted():
    baseline = Arm("baseline", "b", runs=3, attempted=3)
    order = Arm("order", "o", runs=2, attempted=3, error="timed out after 5s")
    out = text(Investigation(arms=[baseline, order], total_tests=2))
    assert "2 of 3 runs" in out
    assert "timed out" in out


def _local(e: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(e)


@pytest.mark.parametrize("n", [2, 3])
def test_a_few_clock_runs_already_cross_a_weekend(n):
    """With the old order the first three dates were Wednesday, Thursday and Friday, so
    a test that breaks at weekends was invisible below four runs."""
    days = {_local(e).weekday() for e in freeze.CLOCK_EPOCHS[:n]}
    assert days & {5, 6}, "no weekend"
    assert days - {5, 6}, "no weekday"


def test_the_first_three_clock_runs_cover_a_month_end_and_both_parities():
    first = freeze.CLOCK_EPOCHS[:3]
    month_ends = [
        e for e in first if (_local(e) + datetime.timedelta(days=1)).month != _local(e).month
    ]
    assert month_ends
    assert {int(e) % 2 for e in first} == {0, 1}


def test_the_first_clock_run_differs_from_the_baseline_in_second_parity():
    """At --runs 1 or 2 the cheapest clock dependency must still be exercised."""
    assert int(freeze.CLOCK_EPOCHS[0]) % 2 != int(freeze.BASELINE_EPOCH) % 2
