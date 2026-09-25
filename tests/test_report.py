"""What gets printed, and the distinctions the wording has to keep."""

from __future__ import annotations

import json

from flake_detective.classify import classify
from flake_detective.report import as_json, text, write_json
from flake_detective.types import Arm, Investigation

TESTS = ["t.py::a", "t.py::b"]


def arm(name: str, runs: int, **failures: int) -> Arm:
    return Arm(name, name, runs, {f"t.py::{k}": v for k, v in failures.items()})


def test_nothing_collected_does_not_read_as_a_clean_bill_of_health():
    out = text(Investigation(arms=[], total_tests=0))
    assert "No tests were collected" in out
    assert "not a clean bill of health" in out


def test_no_flakes_says_what_the_run_count_could_not_see():
    out = text(classify([arm("baseline", 5), arm("order", 5)], TESTS))
    assert "No flaky tests found" in out
    assert "--runs" in out


def test_a_flake_is_printed_with_its_evidence_and_its_fix():
    out = text(classify([arm("baseline", 5), arm("order", 5, a=2)], TESTS))
    assert "t.py::a" in out
    assert "ORDER" in out
    assert "fix:" in out


def test_every_arms_rate_is_shown_beside_the_verdict():
    """So a reader can see the shape the claim was read from, not just the label."""
    out = text(classify([arm("baseline", 5), arm("order", 5, a=2), arm("clock", 5)], TESTS))
    for name in ("baseline", "order", "clock"):
        assert name[:8] in out


def test_broken_tests_are_listed_apart_from_flaky_ones():
    out = text(classify([arm("baseline", 5, a=5), arm("order", 5, a=5)], TESTS))
    assert "broken, not flaky" in out
    assert "No flaky tests found" in out


def test_json_round_trips(tmp_path):
    inv = classify([arm("baseline", 5), arm("order", 5, a=2)], TESTS)
    p = tmp_path / "out" / "r.json"
    write_json(inv, p)
    got = json.loads(p.read_text())
    assert got == as_json(inv)
    assert got["by_cause"] == {"order": 1}
    assert got["flakes"][0]["suggested_fix"]


def test_a_clean_report_states_what_it_could_have_missed():
    """"No flaky tests found" is read as "this suite is clean", and it is not that.

    Seven runs miss a test that fails half the time 0.8% of the time, and one that
    fails a tenth of the time 47.8% of the time. The second number is the one that
    matters and the one nobody works out for themselves, so it is printed.
    """
    from flake_detective.report import text
    from flake_detective.types import Arm, Investigation

    inv = Investigation(
        arms=[Arm("baseline", "identical conditions, repeated", runs=7)], total_tests=40
    )
    out = text(inv)

    assert "No flaky tests found" in out
    assert "0.8%" in out, "the half-the-time bound, 0.5 ** 7"
    assert "47.8%" in out, "the one-in-ten bound, 0.9 ** 7 - the one that should worry you"


def test_the_missed_bound_follows_the_run_count():
    from flake_detective.report import text
    from flake_detective.types import Arm, Investigation

    few = text(Investigation(arms=[Arm("baseline", "d", runs=2)], total_tests=1))
    many = text(Investigation(arms=[Arm("baseline", "d", runs=20)], total_tests=1))

    assert "25.0%" in few, "two runs miss a half-the-time flake a quarter of the time"
    assert "0.0%" in many, "twenty runs make that bound vanish"
