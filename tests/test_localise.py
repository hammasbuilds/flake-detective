"""Localising an order dependence: which earlier test leaves the state behind.

The order arm proves a test passes in some orders and fails in others. That names
the victim, which is the innocent half of the pair. These tests are about naming
the other half - and about refusing to name one when the evidence does not support
it, since a wrong culprit sends somebody to read a file that is fine.
"""

from __future__ import annotations

from pathlib import Path

import pytest


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


@pytest.mark.slow
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


def test_a_test_that_fails_alone_whatever_runs_first_names_nothing(tmp_path):
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

    # It fails on its own, so the search looks for a test that makes it PASS - and
    # with every other test in front it still fails, so nothing is named.
    assert result.direction == "needs"
    assert result.outcome == "not reproducible"
    assert not result.found
    assert result.probes == 2, "alone, then with everything; no bisection"
    assert "still fails with every other test" in result.describe()


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


@pytest.mark.slow
def test_a_test_that_needs_another_is_localised_to_the_one_it_needs(tmp_path):
    """The audit case: test_f needs the environment test_e sets. It fails alone, and
    the old localiser stopped there and called it "broken, not order-dependent" -
    wrong, because it passes the moment test_e runs first. The search now runs the
    other way and names test_e as what it needs."""
    from flake_detective.localise import localise

    repo = _suite(tmp_path)
    (repo / "tests" / "test_env.py").write_text(
        "import os\n\n"
        "def test_e_setup_env():\n    os.environ['FD_LOCALISE_ISO'] = '1'\n\n"
        "def test_f_needs_env():\n    assert os.environ.get('FD_LOCALISE_ISO') == '1'\n",
        encoding="utf-8",
    )
    tests = [f"tests/test_innocent_{i}.py::test_innocent_{i}" for i in range(6)]
    tests += ["tests/test_env.py::test_e_setup_env", "tests/test_env.py::test_f_needs_env"]
    result = localise(repo, "tests/test_env.py::test_f_needs_env", tests, timeout=300)

    assert result.direction == "needs", result.describe()
    assert result.outcome == "single", result.describe()
    assert result.culprits == ["tests/test_env.py::test_e_setup_env"]
    assert "fails on its own and passes only after" in result.describe()
    assert result.as_dict()["direction"] == "needs another test"


def test_localise_works_when_repo_is_a_subfolder_of_the_rootdir(tmp_path):
    """Node ids are relative to the rootdir (where pytest.ini is), not to REPO. Passed
    back unchanged from a subfolder, every probe named a file that does not exist."""
    from flake_detective.localise import localise
    from flake_detective.run import collect_detailed

    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = pkg\n", encoding="utf-8")
    pkg = tmp_path / "pkg" / "tests"
    pkg.mkdir(parents=True)
    (pkg / "test_m.py").write_text(
        "X = []\n\ndef test_a():\n    X.append(1)\n\ndef test_b():\n    assert not X\n",
        encoding="utf-8",
    )
    c = collect_detailed(tmp_path / "pkg")
    assert c.tests == ["pkg/tests/test_m.py::test_a", "pkg/tests/test_m.py::test_b"]
    result = localise(tmp_path / "pkg", c.tests[1], c.tests, timeout=300, rootdir=c.rootdir)
    assert result.direction == "broken", result.describe()
    assert result.culprits == ["pkg/tests/test_m.py::test_a"]
