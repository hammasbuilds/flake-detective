"""Scoring every arm, not just the three `investigate` defaults.

`bench()` has accepted an `arms` argument since the fixture gained a known positive for
each arm, but nothing on the command line could pass it - so four of the eight causes
were unreachable from the CLI and the only score a user could produce was the three-arm
one. `--arms` now exposes it, and this is the end-to-end proof that the extra causes are
both found and named correctly.

Marked slow: it runs the fixture suite under every arm.
"""

from __future__ import annotations

import pytest

from flake_detective import bench as bench_mod
from flake_detective.detective import ALL_ARMS
from flake_detective.run import xdist_available

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def all_arms_score(tmp_path_factory) -> dict:
    if not xdist_available(tmp_path_factory.mktemp("probe")):
        pytest.skip("pytest-xdist is not installed, so the parallel arm cannot run")
    return bench_mod.run(runs=7, arms=ALL_ARMS, jobs=4)


def test_the_parallel_cause_is_found_and_named_parallel(all_arms_score: dict) -> None:
    """The case this rewrite was for.

    At `--jobs 1` the old fixture was missed outright; at the default four it was
    detected and called `nondeterminism`, because four independent pytest processes
    collided on a run-independent marker and the test flipped in the baseline. Both
    readings were correct about a fixture that could not exhibit the cause it claimed.
    """
    assert "error" not in all_arms_score, all_arms_score.get("error")
    reported = all_arms_score["reported"]  # {test_id: cause}
    key = "test_parallel_dependent.py::test_exclusive_use_of_a_shared_file"
    assert key in reported, f"the parallel cause was not detected at all: {reported}"
    assert reported[key] == "parallel", (
        f"detected but misattributed as {reported[key]!r}; a flip seen under the "
        "parallel arm alone is what credits parallelism"
    )


def test_the_holder_is_not_reported_as_flaky(all_arms_score: dict) -> None:
    """It asserts nothing, so any report of it is a false positive."""
    reported = all_arms_score["reported"]
    assert "test_parallel_aaa_holder.py::test_aaa_holds_the_marker" not in reported
    assert all_arms_score["false_positive_rate"] == 0.0


def test_the_isolation_arm_settles_the_needs_other_test_direction(
    all_arms_score: dict,
) -> None:
    """Without the isolation arm this comes back as `order`, the opposite remedy.

    One test needs state another creates; the other is broken by what runs before it.
    "Stop the other test leaking" and "move that setup into a fixture" are opposite
    fixes, so naming the direction is the whole value.
    """
    reported = all_arms_score["reported"]
    key = "test_needs_setup.py::test_needs_the_helper_to_have_run"
    assert reported.get(key) == "needs-other-test", reported


def test_no_cause_is_left_unscoreable_except_the_platform_only_ones(
    all_arms_score: dict,
) -> None:
    """On this platform only timezone and locale should be out of reach.

    Those two are measured no-ops on Windows - setting TZ does not move local time and
    LANG/LC_ALL do not reach the locale. `parallel` used to join them for a different
    reason, pytest-xdist simply not being installed, which is fixable and now fixed in
    the dev extra.
    """
    unscoreable = set(all_arms_score["detail"]["unscoreable"].values())
    assert "parallel" not in unscoreable, (
        "the parallel arm is available here, so its cause must be scored"
    )
    assert unscoreable <= {"timezone", "locale"}, unscoreable
