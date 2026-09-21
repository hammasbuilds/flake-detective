"""The benchmark's answer key, and the scoring that reads it.

A benchmark is only as trustworthy as its fixture, and this one has already been wrong twice:
one "stable" test asserted a literal from a specific Python build and so always failed, and
the hash-dependent test used an eight-element set, which failed under nearly every seed and
so read as broken rather than flaky. Both are checked here by running them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flake_detective import fixture
from flake_detective.run import collect, run_once


@pytest.fixture
def suite(tmp_path: Path) -> Path:
    return fixture.write(tmp_path / "suite")


def test_every_file_is_written(suite: Path):
    assert sorted(p.name for p in suite.glob("*.py")) == sorted(fixture.FILES)


def test_the_answer_key_covers_every_collected_test(suite: Path):
    collected = set(collect(suite))
    keyed = set(fixture.TRUTH)
    # test_bbb_also_appends exists to give the order-dependent test something to collide
    # with; it is not itself a claim, so it is the one test allowed to be unkeyed.
    assert collected - keyed == {"test_order_dependent.py::test_bbb_also_appends"}
    assert keyed - collected == set()


def test_the_key_names_only_real_causes():
    from flake_detective.types import Cause

    valid = {c.value for c in Cause}
    assert {v for v in fixture.TRUTH.values() if v} <= valid


def test_the_stable_tests_really_do_pass(suite: Path):
    """The failure that this catches is a fixture bug masquerading as a tool result."""
    failed = run_once(suite, target="test_stable.py")
    assert failed == set()


def test_the_stable_tests_pass_under_a_different_seed_and_date(suite: Path):
    from flake_detective import freeze

    failed = run_once(suite, target="test_stable.py", hashseed=5, epoch=freeze.CLOCK_EPOCHS[1])
    assert failed == set()


def test_the_hash_test_passes_under_seed_zero(suite: Path):
    """It has to *usually pass* to be flaky rather than broken - that is the whole point
    of the two-element set."""
    assert run_once(suite, target="test_hash_dependent.py", hashseed=0) == set()


def test_scoring_a_perfect_answer():
    found = {k: v for k, v in fixture.TRUTH.items() if v}
    s = fixture.score(found)
    assert s["correct_cause"] == s["flaky_in_fixture"] == 4
    assert s["missed"] == [] and s["false_positives"] == []


def test_scoring_an_empty_answer():
    s = fixture.score({})
    assert s["detected"] == 0
    assert len(s["missed"]) == 4
    assert s["false_positives"] == []


def test_a_wrong_cause_counts_as_detected_but_not_correct():
    found = {"test_clock_dependent.py::test_second_is_even": "nondeterminism"}
    s = fixture.score(found)
    assert s["detected"] == 1
    assert s["correct_cause"] == 0
    assert s["misattributed"]


def test_flagging_a_stable_test_is_a_false_positive():
    s = fixture.score({"test_stable.py::test_plain_arithmetic": "order"})
    assert s["false_positives"] == ["test_stable.py::test_plain_arithmetic"]


def test_shouting_one_cause_at_everything_does_not_score_well():
    """The decoys are what make the benchmark mean anything.

    Without them, a classifier that reported every test as order-dependent would post
    perfect detection and perfect attribution on the one cause it ever names.
    """
    s = fixture.score(dict.fromkeys(fixture.TRUTH, "order"))
    assert s["detected"] == 4
    assert s["correct_cause"] == 1
    assert len(s["false_positives"]) == 5
