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
}

# The answer key. `None` means "must not be reported as flaky at all".
TRUTH = {
    "test_order_dependent.py::test_aaa_first_one_wins": "order",
    "test_hash_dependent.py::test_first_of_a_set_is_stable": "hash-seed",
    "test_clock_dependent.py::test_second_is_even": "clock",
    "test_nondeterministic.py::test_unseeded_random": "nondeterminism",
    "test_stable.py::test_sorted_set_is_deterministic": None,
    "test_stable.py::test_module_state_but_cleans_up": None,
    "test_stable.py::test_clock_but_only_a_duration": None,
    "test_stable.py::test_seeded_random_is_deterministic": None,
    "test_stable.py::test_plain_arithmetic": None,
}


def write(into: Path) -> Path:
    """Materialise the fixture suite. Returns the directory."""
    into.mkdir(parents=True, exist_ok=True)
    for name, body in FILES.items():
        (into / name).write_text(body, encoding="utf-8", newline="")
    return into


def score(found: dict[str, str]) -> dict:
    """Compare a classification against the answer key.

    `found` maps test id -> cause. Anything absent was not reported as flaky.
    """
    flaky = {k: v for k, v in TRUTH.items() if v is not None}
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
        "detected": len(flaky) - len(missed),
        "correct_cause": len(correct),
        "misattributed": misattributed,
        "missed": missed,
        "stable_in_fixture": len(stable),
        "false_positives": false_positives,
    }
