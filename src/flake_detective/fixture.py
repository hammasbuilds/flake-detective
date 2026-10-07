"""A suite with flaky tests whose causes are known, for scoring the classifier.

Every claim this tool makes is a *cause*, and a cause cannot be checked against a real
repository - nobody knows the ground truth for somebody else's flaky test, which is the
reason the tool exists. So the accuracy number comes from a suite written to be flaky in
specified ways.

Two things it must contain in roughly equal measure:

**Flaky tests with a known cause**, one per cause, so classification can be scored.

**Stable tests that look flaky** - one that iterates a sorted set, one that touches module
state and cleans up after itself, one that reads the clock and only compares durations.
Without them the benchmark measures recall and says nothing about false positives, and a
classifier that shouts "order dependence" at everything would score perfectly.
"""

from __future__ import annotations

import time
from pathlib import Path

# Note the cause each file is written to exhibit. The tests are deliberately small and
# obvious - the classifier is being scored on attribution, not on comprehension.

ORDER_DEPENDENT = '''\
"""A test that passes alone and fails after its neighbour. Cause: ORDER."""

_SEEN = []


def test_aaa_first_one_wins():
    _SEEN.append("a")
    assert len(_SEEN) == 1


def test_bbb_also_appends():
    _SEEN.append("b")
    assert "b" in _SEEN
'''

HASH_DEPENDENT = '''\
"""A test that depends on set iteration order. Cause: HASH_SEED."""


def test_first_of_a_set_is_stable():
    # Two elements, so roughly half of all seeds put "alpha" first. Eight elements would
    # make this fail under nearly every seed, which reads as a broken test rather than a
    # flaky one - and that is what a real hash-order flake looks like: it usually passes.
    names = {"alpha", "beta"}
    assert next(iter(names)) == "alpha"
'''

CLOCK_DEPENDENT = '''\
"""A test that reads the wall clock. Cause: CLOCK."""

import time


def test_second_is_even():
    assert int(time.time()) % 2 == 0
'''

NONDETERMINISTIC = '''\
"""A test that flips with nothing changed. Cause: NONDETERMINISM."""

import random


def test_unseeded_random():
    # `random` is seeded from the OS at import, so this differs run to run even with
    # PYTHONHASHSEED fixed and the order unchanged.
    assert random.random() < 0.5
'''

# A template: the expected offset is the one the machine had when the fixture was written.
# That is the whole shape of the real bug - a test that encodes the developer's timezone as
# an assumption - and it is also what makes this flaky rather than broken. The first
# version asserted a UTC property, so it failed in every run on any other machine and was
# correctly reported as "failing, not flaky".
TIMEZONE_DEPENDENT = '''\
"""Encodes the machine's own UTC offset as an assumption. Cause: TIMEZONE.

Passes where it was written and fails when TZ moves. A no-op on Windows, where setting TZ
does not move the interpreter's idea of local time - the timezone arm reports itself
skipped there, so this is scoreable only on POSIX.
"""

import time

# The offset this suite was written under, in seconds east of UTC.
EXPECTED_OFFSET = {offset}


def _offset_now():
    local = time.localtime()
    if local.tm_gmtoff is not None:
        return local.tm_gmtoff
    return -(time.altzone if local.tm_isdst else time.timezone)


def test_local_offset_is_the_one_we_developed_in():
    assert _offset_now() == EXPECTED_OFFSET
'''

LOCALE_DEPENDENT = '''\
"""Asserts case-folding that differs by locale. Cause: LOCALE.

A no-op on Windows for the same reason as the timezone fixture: LANG and LC_ALL do not
reach the locale there, and the arm says so.
"""

import locale


def test_uppercasing_i_is_ascii():
    # In a Turkish locale "i".upper() is "\u0130", not "I". The test is asserting an
    # assumption about the environment, not about the code.
    try:
        locale.setlocale(locale.LC_CTYPE, "")
    except locale.Error:
        pass
    assert "i".upper() == "I"
'''

PARALLEL_DEPENDENT = '''\
"""Fails when ANOTHER test holds a resource it wants. Cause: PARALLEL.

The parallel arm exists for "two tests that each want the same fixed resource - a port,
a temp path, a database name - and got away with it while they ran one after another".
This fixture used to be a single test contending with *itself*, which under `-n` cannot
happen: xdist runs each test once, on one worker, so there was never a second copy. The
arm therefore never reproduced it, and the cause scored as missed.

Its partner, test_parallel_aaa_holder.py, holds the marker for a couple of seconds and
asserts nothing, so it can never itself be reported flaky. Collected first, it is handed
to a worker immediately; this test then runs on another worker while the marker is held.
Run serially the holder has already released it, and this passes.
"""

import os

MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parallel.lock")


def test_exclusive_use_of_a_shared_file():
    assert not os.path.exists(MARKER), "another test is holding the marker"
'''

PARALLEL_HOLDER = '''\
"""Holds a shared marker briefly. Asserts nothing, so it is never flaky itself.

The marker is keyed on the pytest RUN, not on this file alone. `PYTEST_XDIST_TESTRUNUID`
is the same for every worker of one `-n` run and absent without xdist, so the workers of
a single run contend while independent pytest processes do not. That distinction is the
whole fixture: the benchmark runs up to four pytest processes at once, and with a
run-independent path those processes collided too - the test flipped in the BASELINE and
was reported as nondeterminism, a correct reading of a fixture that was lying.
"""

import os
import time

RUN = os.environ.get("PYTEST_XDIST_TESTRUNUID") or str(os.getpid())
MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "parallel.lock")


def test_aaa_holds_the_marker():
    # Written only when this run uses workers: serially there is nobody to contend with,
    # and leaving a marker behind for the next test to trip over would make this an order
    # dependence instead of a parallel one.
    if not os.environ.get("PYTEST_XDIST_TESTRUNUID"):
        return
    with open(MARKER, "w", encoding="utf-8") as handle:
        handle.write(RUN)
    try:
        time.sleep(2.0)
    finally:
        try:
            os.remove(MARKER)
        except OSError:
            pass
'''

NEEDS_ANOTHER_TEST = '''\
"""Fails ALONE and passes after its neighbour. Cause: NEEDS_TEST (the isolation arm).

The opposite direction to the order fixture, which passes alone and fails after. The two
remedies are opposite - stop the other test leaking, versus move that setup into a fixture
- so the classifier has to tell them apart, and this is the half it was never scored on.
"""

import test_needs_setup_helper as helper


def test_needs_the_helper_to_have_run():
    assert helper.PREPARED, "nothing prepared the state this test reads"
'''

NEEDS_ANOTHER_TEST_HELPER = '''\
"""The test that prepares the state. Stable itself."""

PREPARED = False


def test_aaa_prepares():
    global PREPARED
    PREPARED = True
    assert True
'''

STABLE = '''\
"""Tests that look like the flaky ones and are not. None of these may be flagged."""

import time

_STATE = []


def test_sorted_set_is_deterministic():
    names = {"alpha", "beta", "gamma", "delta", "epsilon"}
    # Sorted, so hash order cannot reach it.
    assert sorted(names)[0] == "alpha"


def test_module_state_but_cleans_up():
    _STATE.append("x")
    try:
        assert _STATE == ["x"]
    finally:
        _STATE.clear()


def test_clock_but_only_a_duration():
    start = time.time()
    total = sum(range(1000))
    # A duration, not an absolute time: independent of when it runs.
    assert time.time() - start < 60
    assert total == 499500


def test_seeded_random_is_deterministic():
    import random as r

    # Not `< 0.5` - that pins a literal from one Python version and would simply always
    # fail, which is a broken test, not a stable one. Two seeded streams agreeing is the
    # property actually being claimed.
    assert [r.Random(1234).random() for _ in range(3)] == [r.Random(1234).random()] * 3


def test_plain_arithmetic():
    assert sum(range(10)) == 45
'''

FILES = {
    "test_order_dependent.py": ORDER_DEPENDENT,
    "test_hash_dependent.py": HASH_DEPENDENT,
    "test_clock_dependent.py": CLOCK_DEPENDENT,
    "test_nondeterministic.py": NONDETERMINISTIC,
    "test_stable.py": STABLE,
    # One known positive per arm that had none. See TRUTH for which are scoreable on
    # which platform.
    "test_timezone_dependent.py": TIMEZONE_DEPENDENT,
    "test_locale_dependent.py": LOCALE_DEPENDENT,
    "test_parallel_aaa_holder.py": PARALLEL_HOLDER,
    "test_parallel_dependent.py": PARALLEL_DEPENDENT,
    "test_needs_setup_helper.py": NEEDS_ANOTHER_TEST_HELPER,
    "test_needs_setup.py": NEEDS_ANOTHER_TEST,
}

# The answer key. `None` means "must not be reported as flaky at all".
TRUTH = {
    "test_order_dependent.py::test_aaa_first_one_wins": "order",
    # The other half of the order pair. It passes whatever runs before it, so it is
    # stable - and a classifier that blamed the culprit instead of the victim, or
    # both, would be caught here as a false positive.
    "test_order_dependent.py::test_bbb_also_appends": None,
    "test_hash_dependent.py::test_first_of_a_set_is_stable": "hash-seed",
    "test_clock_dependent.py::test_second_is_even": "clock",
    "test_nondeterministic.py::test_unseeded_random": "nondeterminism",
    "test_stable.py::test_sorted_set_is_deterministic": None,
    "test_stable.py::test_module_state_but_cleans_up": None,
    "test_stable.py::test_clock_but_only_a_duration": None,
    "test_stable.py::test_seeded_random_is_deterministic": None,
    "test_stable.py::test_plain_arithmetic": None,
    # The four arms that had no known positive at all. Their detection and attribution
    # rates were undefined while `--arms` offered them.
    #
    # `timezone` and `locale` are no-ops on Windows - setting TZ does not move local time
    # there and LANG/LC_ALL do not reach the locale - so on Windows these two are expected
    # NOT to be found, and `skipped_arms` says why. ENVIRONMENT_ONLY lists them so a
    # scorer can require them only where the arm can run.
    "test_timezone_dependent.py::test_local_offset_is_the_one_we_developed_in": "timezone",
    "test_locale_dependent.py::test_uppercasing_i_is_ascii": "locale",
    "test_parallel_dependent.py::test_exclusive_use_of_a_shared_file": "parallel",
    # Holds the marker so the test above has something to contend with. It asserts
    # nothing at all, so reporting it as flaky would be a false positive.
    "test_parallel_aaa_holder.py::test_aaa_holds_the_marker": None,
    # Fails alone, passes after its helper: the needs-another-test direction, which is the
    # opposite remedy to the order fixture and was never scored.
    "test_needs_setup.py::test_needs_the_helper_to_have_run": "needs-other-test",
    "test_needs_setup_helper.py::test_aaa_prepares": None,
}

# Causes whose arm cannot run on every platform. A scorer should require these only where
# the corresponding arm reports itself available, and `skipped_arms` is how it says.
ENVIRONMENT_ONLY = {"timezone", "locale"}

# Causes needing more than one worker, so a serial run cannot reproduce them.
PARALLEL_ONLY = {"parallel"}


def write(into: Path) -> Path:
    """Materialise the fixture suite. Returns the directory."""
    into.mkdir(parents=True, exist_ok=True)
    local = time.localtime()
    offset = local.tm_gmtoff
    if offset is None:
        offset = -(time.altzone if local.tm_isdst else time.timezone)
    for name, body in FILES.items():
        # Only the timezone fixture is a template, and it has to be calibrated to the
        # machine writing it: a test that passes here and fails under a different TZ is
        # timezone-dependent, whereas one asserting a fixed offset is simply wrong
        # everywhere else and gets reported as failing rather than flaky.
        if "{offset}" in body:
            body = body.replace("{offset}", str(offset))
        (into / name).write_text(body, encoding="utf-8", newline="")
    return into


def score(found: dict[str, str], searched: set[str] | None = None) -> dict:
    """Compare a classification against the answer key.

    `found` maps test id -> cause. Anything absent was not reported as flaky.

    `searched` is the set of causes whose arm actually ran. A cause nobody looked for is
    not a detection failure: on Windows the timezone and locale arms report themselves
    unavailable and `parallel` needs pytest-xdist in the target's environment, so three of
    the eight known positives cannot be found there at all. Counting them as misses made
    the headline rate a property of the platform rather than of the classifier. They are
    reported under `unscoreable` and excluded from both rates.

    Omitted, every cause is assumed searched, which is the previous behaviour.
    """
    everything = {k: v for k, v in TRUTH.items() if v is not None}
    if searched is None:
        flaky = everything
        unscoreable: dict[str, str] = {}
    else:
        flaky = {k: v for k, v in everything.items() if v in searched}
        unscoreable = {k: v for k, v in everything.items() if v not in searched}
    stable = [k for k, v in TRUTH.items() if v is None]

    def match(key: str) -> str | None:
        # pytest ids may carry a directory prefix depending on how it was invoked.
        for got, cause in found.items():
            if got.endswith(key) or key.endswith(got):
                return cause
        return None

    detected = {k: match(k) for k in flaky}
    correct = {k: c for k, c in detected.items() if c == flaky[k]}
    misattributed = {k: c for k, c in detected.items() if c and c != flaky[k]}
    missed = [k for k, c in detected.items() if c is None]
    false_positives = [k for k in stable if match(k)]

    return {
        "flaky_in_fixture": len(flaky),
        # Known positives whose arm never ran, so neither rate can speak for them.
        "unscoreable": unscoreable,
        "detected": len(flaky) - len(missed),
        "correct_cause": len(correct),
        "misattributed": misattributed,
        "missed": missed,
        "stable_in_fixture": len(stable),
        "false_positives": false_positives,
    }
