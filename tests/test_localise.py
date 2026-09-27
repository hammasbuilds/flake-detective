"""Localising an order dependence: which earlier test leaves the state behind.

The order arm proves a test passes in some orders and fails in others. That names
the victim, which is the innocent half of the pair. These tests are about naming
the other half - and about refusing to name one when the evidence does not support
it, since a wrong culprit sends somebody to read a file that is fine.
"""

from __future__ import annotations

from pathlib import Path


def _suite(root: Path, extra: str = "") -> Path:
    """A real little suite: one culprit, one victim, and several innocents."""
    tests = root / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        (tests / f"test_innocent_{i}.py").write_text(
            f"def test_innocent_{i}():\n    assert {i} == {i}\n", encoding="utf-8"
        )
    (tests / "test_culprit.py").write_text(
        "import shared_state\n\n\ndef test_culprit_leaves_state():\n"
        "    shared_state.SEEN.append(1)\n    assert shared_state.SEEN\n",
        encoding="utf-8",
    )
    (tests / "test_victim.py").write_text(
        "import shared_state\n\n\ndef test_victim():\n    assert shared_state.SEEN == []\n",
        encoding="utf-8",
    )
    (root / "shared_state.py").write_text("SEEN: list[int] = []\n", encoding="utf-8")
    (root / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent))\n" + extra,
        encoding="utf-8",
    )
    return root


def test_the_culprit_behind_an_order_dependence_is_named(tmp_path):
    """The stated limitation: "Order dependence is found, not localised."

    Reporting that a test fails in some orders names the one test in the pair that
    is innocent, and leaves whoever reads it to find the guilty one by hand.
    """
    from flake_detective.localise import localise

    repo = _suite(tmp_path)
    tests = [f"tests/test_innocent_{i}.py::test_innocent_{i}" for i in range(6)]
    tests += [
        "tests/test_culprit.py::test_culprit_leaves_state",
        "tests/test_victim.py::test_victim",
    ]

    result = localise(repo, "tests/test_victim.py::test_victim", tests, timeout=300)

    assert result.outcome == "single", result.describe()
    assert result.culprits == ["tests/test_culprit.py::test_culprit_leaves_state"]
    # log2(7) halvings plus the two checks at the start, not a scan of all seven.
    assert result.probes <= 9, f"bisection took {result.probes} runs"


def test_a_test_that_fails_alone_is_not_called_order_dependent(tmp_path):
    """Bisecting a broken test would "find" whichever half happened to be tried
    first, and send somebody to read a file that is fine."""
    from flake_detective.localise import localise

    repo = _suite(tmp_path)
    (repo / "tests" / "test_broken.py").write_text(
        "def test_broken():\n    assert False\n", encoding="utf-8"
    )
    result = localise(
        repo,
        "tests/test_broken.py::test_broken",
        ["tests/test_innocent_0.py::test_innocent_0", "tests/test_broken.py::test_broken"],
        timeout=300,
    )

    assert result.outcome == "victim fails alone"
    assert not result.found
    assert result.probes == 1, "it should stop at the first probe"


def test_a_victim_nothing_reproduces_is_reported_as_such(tmp_path):
    """A localiser that always names something sometimes names the wrong thing."""
    from flake_detective.localise import localise

    repo = _suite(tmp_path)
    result = localise(
        repo,
        "tests/test_innocent_1.py::test_innocent_1",
        [f"tests/test_innocent_{i}.py::test_innocent_{i}" for i in range(6)],
        timeout=300,
    )

    assert result.outcome == "not reproducible"
    assert result.culprits == []
