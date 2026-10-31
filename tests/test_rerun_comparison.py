"""docs/COMPARISON.md agrees with docs/rerun-comparison.json.

The claim this page makes about another tool is specific - that a re-runner cannot reach
an order-dependent flake, masks two of four, and names no cause - so each part is checked
against the recorded run rather than against the prose.

The guard that matters most is the restriction: both tools touched four tests and not the
same four, and an earlier draft of the page published that as a tie. The test below fails
if the two counts are ever compared unrestricted again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "COMPARISON.md"
ARTEFACT = ROOT / "docs" / "rerun-comparison.json"

pytestmark = pytest.mark.skipif(
    not (DOC.exists() and ARTEFACT.exists()),
    reason="docs/ is not shipped with the tests",
)


@pytest.fixture(scope="module")
def data() -> dict:
    return json.loads(ARTEFACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


def test_the_versions_and_settings_named_are_the_ones_that_ran(data, doc):
    for package, version in data["baseline_versions"].items():
        if package == "flaky":
            continue  # installed in the baseline venv, not used in the published run
        assert f"`{package}` {version}" in doc or f"`{package}` {version}" in doc, package
    # Every occurrence, not just one. The document names --reruns twice, so a guard
    # that only checked presence passed when one of them was changed.
    import re  # noqa: PLC0415

    quoted = set(re.findall(r"--reruns=(\d+)", doc))
    assert quoted == {str(data["reruns_per_failure"])}, (
        f"the document quotes --reruns={sorted(quoted)}, the run used {data['reruns_per_failure']}"
    )
    assert f"**{data['suite_runs_under_the_rerunner']} times**" in doc


def test_the_fixture_counts_are_the_artefacts(data, doc):
    assert f"of {data['stable_but_flaky_looking']} stable-but-flaky-looking tests" in doc
    assert data["known_flaky"] == 8, "the answer key changed size; re-read the page"
    assert "eight specified ways" in doc


def test_this_tool_detected_and_named_every_scoreable_cause(data, doc):
    fd = data["flake_detective"]
    scoreable = data["causes_this_platform_can_score"]
    assert fd["detected"] == len(scoreable)
    assert fd["correct_cause"] == len(scoreable)
    assert fd["false_positives"] == []
    assert (
        f"| detected, of the {len(scoreable)} both sides can be scored on | "
        f"**{fd['detected']}** |" in doc
    )
    assert (
        f"| **cause named correctly** | **{fd['correct_cause']} of "
        f"{len(scoreable)}** | **names no cause** |" in doc
    )


def test_the_rerunners_figures_are_the_restricted_ones(data, doc):
    """The guard against the tie this page nearly published."""
    rerun = data["pytest_rerunfailures"]
    assert rerun["noticed_of_the_common_set"] < rerun["noticed"], (
        "the restricted and unrestricted counts are equal, so this guard proves nothing; "
        "re-read the page"
    )
    assert (
        f"**{rerun['noticed_of_the_common_set']}** noticed, "
        f"**{rerun['masked_of_the_common_set']}** masked" in doc
    )
    assert "not the same four" in doc
    assert "a tie that does not exist" in doc


def test_the_order_dependent_flake_was_never_retried(data, doc):
    """The sharpest claim: unreachable, not missed."""
    rerun = data["pytest_rerunfailures"]
    never = rerun["never_retried_in_the_common_set"]
    assert len(never) == 1, never
    assert "order_dependent" in never[0]
    assert "| **never retried** - of the four scoreable here | **1** | **order** |" in doc
    assert "invisible to it by construction" in doc


def test_the_masked_tests_are_the_two_that_flip_under_repetition(data, doc):
    rerun = data["pytest_rerunfailures"]
    masked = {Path(t.split("::")[0]).stem for t in rerun["masked_tests"]}
    assert masked == {"test_clock_dependent", "test_nondeterministic"}, masked
    assert (
        "| **masked** - retried and the session ended green | "
        f"**{rerun['masked']}** | clock, nondeterminism |" in doc
    )


def test_the_rerunner_names_no_cause_and_the_page_says_so(data, doc):
    assert data["pytest_rerunfailures"]["correct_cause"] is None
    assert data["pytest_rerunfailures"]["names_a_cause"] is False
    assert data["flake_detective"]["names_a_cause"] is True
    assert "names no cause" in doc


def test_neither_tool_flagged_a_stable_test(data, doc):
    assert data["flake_detective"]["false_positives"] == []
    assert data["pytest_rerunfailures"]["false_positives"] == 0
    assert "| false positives (of 8 stable-but-flaky-looking tests) | **0** | **0** |" in doc


def test_the_unscoreable_causes_are_named_and_excluded(data, doc):
    """Three known positives cannot be found on this platform; the page says which."""
    unscoreable = data["flake_detective"]["unscoreable"]
    assert set(unscoreable.values()) >= {"timezone", "locale", "parallel"}
    for cause in ("timezone", "locale", "parallel"):
        assert cause in doc, cause
    assert (
        "excluded\nfrom both sides rather than counted as misses" in doc
        or "excluded from both sides rather than counted as misses" in doc
    )


def test_the_hash_seed_rates_quoted_are_a_real_arm_split(data, doc):
    """The evidence block is the argument, so its shape is held to.

    A rate of 0.00 under repetition and non-zero under one varied thing IS the diagnosis;
    if the quoted block ever shows a non-zero baseline the sentence beside it is wrong.
    """
    assert "baseline 0.00" in doc
    import re

    block = re.search(r"rates\s+: baseline 0\.00\s+order 0\.00\s+hashseed ([\d.]+)", doc)
    assert block, "the quoted rates block has changed shape"
    assert float(block.group(1)) > 0, "the hashseed arm shows no effect in the quoted block"


def test_the_limits_section_still_names_the_obvious_objection(doc):
    limits = doc.split("## What this does not show", 1)
    assert len(limits) == 2, "the limits section is gone"
    for phrase in (
        "not a diagnostic tool",
        "written by the same hand",
        "choices I made",
        "no answer key exists",
    ):
        assert phrase in limits[1], f"the limits no longer say: {phrase}"
