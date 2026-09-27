"""What a run observed, read from pytest's own reports - each case here was a silent wrong answer.

Every test below runs real pytest in a subprocess, the way the tool does.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from flake_detective import run as run_mod
from flake_detective.detective import Options, investigate
from flake_detective.report import as_json
from flake_detective.types import Cause


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def test_a_setup_error_is_a_failure_not_a_pass(tmp_path):
    """`-rf` never printed ERROR lines, so a test whose fixture raised read as passing."""
    d = _write(
        tmp_path,
        {
            "test_e.py": "import pytest\n\n"
            "@pytest.fixture\ndef broken():\n    raise RuntimeError('boom')\n\n"
            "def test_uses_it(broken):\n    pass\n\n"
            "def test_fine():\n    pass\n"
        },
    )
    obs, why = run_mod.run_observed(d)
    assert obs is not None, why
    assert obs.failed == {"test_e.py::test_uses_it"}
    assert obs.errored == {"test_e.py::test_uses_it"}
    assert obs.passed == {"test_e.py::test_fine"}


def test_a_teardown_error_is_a_failure(tmp_path):
    d = _write(
        tmp_path,
        {
            "test_t.py": "import pytest\n\n"
            "@pytest.fixture\ndef leaky():\n    yield 1\n    raise RuntimeError('teardown')\n\n"
            "def test_passes_then_errors(leaky):\n    assert leaky == 1\n"
        },
    )
    assert run_mod.run_once(d) == {"test_t.py::test_passes_then_errors"}


def test_a_fixture_level_order_dependence_is_found(tmp_path):
    """The audit's case: the victim errors in setup in every baseline run. It appeared
    nowhere - not as flaky, not even as broken."""
    d = _write(
        tmp_path,
        {
            "test_f.py": "import pytest\n"
            "FLAG = {'set': False}\n\n"
            "def test_c_sets_flag():\n    FLAG['set'] = True\n\n"
            "@pytest.fixture\ndef needs_clean():\n"
            "    if FLAG['set']:\n        raise RuntimeError('dirty')\n    return 1\n\n"
            "def test_d_victim(needs_clean):\n    assert needs_clean == 1\n"
        },
    )
    inv = investigate(d, "", Options(runs=6, arms=("order", "isolation"), order_seed=4))
    assert inv.ok
    causes = {f.test_id: f for f in inv.flakes}
    victim = causes["test_f.py::test_d_victim"]
    assert victim.cause is Cause.ORDER and victim.directed
    assert victim.errored


def test_a_test_that_did_not_run_is_not_a_pass(tmp_path):
    """With `addopts = -x` a failure stopped the run and later tests never ran; they
    used to count as passes. -x is neutralised, and tests not observed are not counted."""
    d = _write(
        tmp_path,
        {
            "pytest.ini": "[pytest]\naddopts = -x\n",
            "test_x.py": "def test_0_fails():\n    assert False\n\n"
            "def test_1_later_fails():\n    assert False\n\n"
            "def test_2_later_passes():\n    pass\n",
        },
    )
    obs, why = run_mod.run_observed(d)
    assert obs is not None, why
    assert obs.failed == {"test_x.py::test_0_fails", "test_x.py::test_1_later_fails"}
    assert obs.passed == {"test_x.py::test_2_later_passes"}


@pytest.mark.parametrize("opt", ["--maxfail=1", "--sw", "--lf", "--ff", "--nf"])
def test_stop_early_and_history_options_in_addopts_are_neutralised(tmp_path, opt):
    d = _write(
        tmp_path,
        {
            "pytest.ini": f"[pytest]\naddopts = {opt}\n",
            "test_x.py": "def test_a():\n    assert False\n\n"
            "def test_b():\n    assert False\n\n"
            "def test_c():\n    pass\n",
        },
    )
    ids = ["test_x.py::test_c", "test_x.py::test_b", "test_x.py::test_a"]
    for _ in range(2):  # a second run is where --lf/--ff/--sw would use the history
        obs, why = run_mod.run_observed(d, order=ids)
        assert obs is not None, why
        assert obs.seen == set(ids), (opt, obs.outcomes)


def test_skips_and_xfails_are_not_failures_and_skips_are_not_observations(tmp_path):
    d = _write(
        tmp_path,
        {
            "test_s.py": "import pytest\n\n"
            "@pytest.mark.skip\ndef test_skipped():\n    pass\n\n"
            "@pytest.mark.xfail\ndef test_expected():\n    assert False\n\n"
            "def test_runtime_skip():\n    pytest.skip('no')\n"
        },
    )
    obs, why = run_mod.run_observed(d)
    assert obs is not None, why
    assert obs.failed == set()
    assert obs.seen == {"test_s.py::test_expected"}


def test_a_test_id_containing_space_dash_space_is_kept_whole(tmp_path):
    """The summary line separates id from message with " - ", so the old parser cut
    `test_b[with space - dash]` short; it matched no collected test and its four
    baseline failures were never counted."""
    d = _write(
        tmp_path,
        {
            "test_sp.py": "import pytest\n\n"
            "@pytest.mark.parametrize('v', [1], ids=['with space - dash'])\n"
            "def test_b(v):\n    assert False\n"
        },
    )
    assert run_mod.collect(d) == ["test_sp.py::test_b[with space - dash]"]
    assert run_mod.run_once(d) == {"test_sp.py::test_b[with space - dash]"}


def test_collection_does_not_depend_on_the_projects_verbosity(tmp_path):
    """`-q` in addopts makes --collect-only print "file: count" lines with no node ids."""
    d = _write(
        tmp_path,
        {
            "pytest.ini": "[pytest]\naddopts = -q\n",
            "test_q.py": "def test_a():\n    pass\n\ndef test_b():\n    pass\n",
        },
    )
    assert run_mod.collect(d) == ["test_q.py::test_a", "test_q.py::test_b"]


def test_a_repo_below_the_rootdir_runs_its_order_arm(tmp_path):
    """mono/pytest.ini with `investigate mono/pkg`: ids come back relative to mono, runs
    start in mono/pkg, and every order run failed with "file or directory not found"."""
    _write(
        tmp_path,
        {
            "pytest.ini": "[pytest]\ntestpaths = pkg\n",
            "pkg/tests/test_m.py": "X = []\n\ndef test_a():\n    X.append(1)\n\n"
            "def test_b():\n    assert not X\n",
        },
    )
    repo = tmp_path / "pkg"
    c = run_mod.collect_detailed(repo)
    assert not c.error, c.error
    assert Path(c.rootdir) == tmp_path
    rev = list(reversed(c.tests))
    assert run_mod.run_once(repo, order=rev, rootdir=c.rootdir) == set()

    inv = investigate(repo, "", Options(runs=4, arms=("order",), order_seed=1))
    assert inv.ok, [a.error for a in inv.arms]
    order = next(a for a in inv.arms if a.name == "order")
    assert order.runs == order.attempted == 4


def test_needs_another_test_end_to_end_with_localise_in_report_and_json(tmp_path):
    d = _write(
        tmp_path,
        {
            "test_iso.py": "import os\n\n"
            "def test_e_setup_env():\n    os.environ['FD_TEST_OBSERVE_ISO'] = '1'\n\n"
            "def test_f_needs_env():\n    assert os.environ.get('FD_TEST_OBSERVE_ISO') == '1'\n",
        },
    )
    inv = investigate(d, "", Options(runs=6, arms=("order",), order_seed=4, localise=True))
    (f,) = [f for f in inv.flakes if f.test_id.endswith("test_f_needs_env")]
    assert f.cause is Cause.NEEDS_TEST
    assert f.culprits == ["test_iso.py::test_e_setup_env"]
    row = next(r for r in as_json(inv)["flakes"] if r["test"] == f.test_id)
    assert row["needs"] == ["test_iso.py::test_e_setup_env"]
    assert row["localisation"]["direction"] == "needs another test"


@pytest.mark.skipif(importlib.util.find_spec("xdist") is None, reason="needs pytest-xdist")
def test_xdist_in_addopts_does_not_distribute_the_baseline(tmp_path):
    d = _write(
        tmp_path,
        {
            "pytest.ini": "[pytest]\naddopts = -n 2\n",
            "test_w.py": "import os\n\ndef test_where():\n"
            "    assert 'PYTEST_XDIST_WORKER' not in os.environ\n",
        },
    )
    assert run_mod.run_once(d) == set()
