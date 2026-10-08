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
    """Every collected test is keyed, so the count the benchmark prints is the count
    pytest collects. It used to say 9 tests while pytest collected 10."""
    collected = set(collect(suite))
    keyed = set(fixture.TRUTH)
    assert collected == keyed


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
    # Derived, not hardcoded: the fixture gained a known positive for each of the four
    # arms that had none, and a literal 4 here broke a test that was not about the count.
    assert s["correct_cause"] == s["flaky_in_fixture"] == len(found)
    assert s["missed"] == [] and s["false_positives"] == []


def test_scoring_an_empty_answer():
    s = fixture.score({})
    flaky = [k for k, v in fixture.TRUTH.items() if v]
    assert s["detected"] == 0
    assert len(s["missed"]) == len(flaky)
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
    flaky = [k for k, v in fixture.TRUTH.items() if v]
    stable = [k for k, v in fixture.TRUTH.items() if v is None]
    assert s["detected"] == len(flaky)
    # Exactly one cause is right, whichever single cause is shouted - that is the point of
    # the decoys, and it must not grow just because the fixture did.
    assert s["correct_cause"] == sum(1 for v in fixture.TRUTH.values() if v == "order")
    assert len(s["false_positives"]) == len(stable)


def test_every_arm_has_a_known_positive():
    """A cause the fixture cannot exhibit is a cause the tool is never scored on.

    `ALL_ARMS` has seven members and `TRUTH` covered four causes, so timezone, locale,
    parallel and isolation had never been scored against a single known positive - their
    detection and attribution rates were undefined while `--arms` offered them, and
    classify.py credits a flip under `parallel` alone to parallelism.
    """
    from flake_detective.classify import ARM_CAUSE
    from flake_detective.detective import ALL_ARMS

    covered = {v for v in fixture.TRUTH.values() if v}
    for arm in ALL_ARMS:
        cause = ARM_CAUSE.get(arm)
        assert cause is not None, f"{arm} maps to no cause"
        assert cause.value in covered, (
            f"the {arm} arm has no known positive in the fixture, so its detection and "
            f"attribution rates are undefined"
        )


def test_a_cause_whose_arm_did_not_run_is_not_counted_as_a_miss():
    """Otherwise the headline rate measures the platform, not the classifier.

    Giving the four unscored arms a fixture each dropped the reported detection from 100%
    to 62.5% on Windows, where the timezone and locale arms are no-ops and `parallel`
    needs pytest-xdist. The three "missed" tests were exactly those three.
    """
    flaky = {k: v for k, v in fixture.TRUTH.items() if v}
    # Nothing found, and only the clock arm searched for.
    scored = fixture.score({}, searched={"clock"})
    assert scored["flaky_in_fixture"] == 1, "only the clock cause was scoreable"
    assert len(scored["missed"]) == 1
    assert set(scored["unscoreable"].values()) == {v for v in flaky.values()} - {"clock"}

    # And with no `searched` argument the old behaviour holds: everything counts.
    everything = fixture.score({})
    assert everything["flaky_in_fixture"] == len(flaky)
    assert everything["unscoreable"] == {}


def test_the_timezone_fixture_passes_in_the_machines_own_zone():
    """It has to be flaky, not broken.

    The first version asserted `localtime().tm_hour == gmtime().tm_hour`, true only at
    offset zero, so it failed in every run here and was correctly reported under
    `always_failed` - "failing, not flaky". A timezone-dependent test passes where it was
    written and fails when TZ moves, so the fixture bakes in the machine's own offset.
    """
    import subprocess
    import sys
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="fd-tz-test-"))
    fixture.write(root)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "test_timezone_dependent.py", "-q"],
        capture_output=True,
        cwd=root,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, (
        "the timezone fixture fails in its own zone, which makes it broken rather than "
        "flaky: " + done.stdout.decode("utf-8", "replace")[-400:]
    )


def test_the_parallel_fixture_needs_a_second_test_to_contend_with():
    """One test cannot contend with itself under `-n`, so a pair is the whole point.

    The parallel arm exists for "two tests that each want the same fixed resource and
    got away with it while they ran one after another". The fixture was a single test
    asserting no other copy of itself held a marker - and xdist runs each test once, on
    one worker, so no second copy ever existed. The arm never reproduced it and the
    cause scored as missed at `--jobs 1`; at the default four it was detected for the
    wrong reason, because four independent pytest processes collided on the marker and
    the test flipped in the BASELINE, which reads as nondeterminism.
    """
    holder = fixture.FILES["test_parallel_aaa_holder.py"]
    contender = fixture.FILES["test_parallel_dependent.py"]

    # The holder writes the marker; the contender only reads it. A contender that also
    # wrote it would be racing itself again.
    assert 'open(MARKER, "w"' in holder
    assert 'open(MARKER, "w"' not in contender
    assert "os.path.exists(MARKER)" in contender

    # Keyed on the pytest run, so the workers of one `-n` run contend and independent
    # pytest processes do not. Without this the benchmark's own concurrency poisoned
    # the baseline.
    assert "PYTEST_XDIST_TESTRUNUID" in holder

    # Collected first, so it is handed to a worker before the contender runs.
    assert "test_parallel_aaa_holder.py" < "test_parallel_dependent.py"

    # And it must never be reported flaky itself: it asserts nothing.
    assert fixture.TRUTH["test_parallel_aaa_holder.py::test_aaa_holds_the_marker"] is None
    assert "assert" not in holder.split('"""', 2)[-1]


def test_the_holder_does_nothing_when_the_suite_is_not_parallel():
    """Serially there is nobody to contend with, and a leftover marker would be worse.

    If the holder wrote the marker in a serial run, the contender would trip over it and
    the cause would read as an ORDER dependence - the wrong answer, from a fixture that
    created the wrong hazard.
    """
    holder = fixture.FILES["test_parallel_aaa_holder.py"]
    body = holder.split("def test_aaa_holds_the_marker():", 1)[1]
    guard = body.index("return")
    write = body.index('open(MARKER, "w"')
    assert guard < write, "the holder must return before writing when xdist is absent"
    assert 'os.environ.get("PYTEST_XDIST_TESTRUNUID")' in body[:guard]


@pytest.mark.skipif(
    not (Path(__file__).resolve().parent.parent / "README.md").exists(),
    reason="README.md is not shipped with the tests",
)
def test_the_readme_counts_match_the_fixture():
    """The published table has to be the fixture, not a memory of it.

    Adding the parallel holder moved the stable count from 7 to 8, and the README quotes
    it twice. A count in prose that nothing checks is a count that drifts: this file's own
    history has an arm whose rates were undefined while the README offered it.
    """
    import pathlib
    import re

    readme = (pathlib.Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8"
    )
    flaky = [k for k, v in fixture.TRUTH.items() if v]
    stable = [k for k, v in fixture.TRUTH.items() if v is None]

    # Parenthesised on purpose. Written as `assert f"..."` with the second half on the
    # next line, the assert ends at the newline - it tests that a non-empty string is
    # truthy, and the remainder is a discarded expression. That version passed while
    # saying nothing, which is the one failure mode this whole file exists to catch.
    sentence = (
        f"**{len(flaky)} flaky tests, one per cause the tool can name, and "
        f"{len(stable)} stable ones**"
    )
    assert sentence in readme, (
        f"the README does not say {len(flaky)} flaky and {len(stable)} stable"
    )

    # Scoreable here = every cause whose arm can run on this platform.
    unrunnable = fixture.ENVIRONMENT_ONLY
    scoreable = {v for v in fixture.TRUTH.values() if v} - unrunnable
    row = re.search(r"\| scoreable causes \| \*\*(\d+)\*\* of (\d+) \|", readme)
    assert row, "the scoreable-causes row is gone from the README"
    assert int(row.group(1)) == len(scoreable), (
        f"README says {row.group(1)} scoreable causes; the fixture has {len(scoreable)} "
        f"once {sorted(unrunnable)} are excluded"
    )
    assert int(row.group(2)) == len({v for v in fixture.TRUTH.values() if v})

    flagged = re.search(r"\| stable tests flagged \| \*\*0\*\* of (\d+) \|", readme)
    assert flagged, "the stable-tests-flagged row is gone from the README"
    assert int(flagged.group(1)) == len(stable), (
        f"README says 0 of {flagged.group(1)}; the fixture has {len(stable)} stable tests"
    )
