"""The README's description of the fixture is counted from the fixture.

The README said the benchmark suite was "written to be flaky in four specified ways" in
its sample-output section while its own Results section, 200 lines below, said "8 flaky
tests, one per cause the tool can name". Both were written from a measurement; the first
was written when there were four causes and never revisited as four more were added. The
two numbers contradicted each other in one file and nothing compared either to
`fixture.TRUTH`, which is where the answer actually lives.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from flake_detective import fixture

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

pytestmark = pytest.mark.skipif(
    not README.exists(), reason="README.md is not shipped with the tests"
)

WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}  # fmt: skip


def test_the_readme_states_the_number_of_planted_causes() -> None:
    planted = sum(1 for cause in fixture.TRUTH.values() if cause)
    text = README.read_text(encoding="utf-8")
    stated = re.search(r"flaky in (\w+) specified ways", text)
    assert stated, "the README no longer describes the fixture in the known shape"
    assert stated.group(1) == WORDS[planted], (
        f"the README says the fixture is flaky in {stated.group(1)} ways; "
        f"fixture.TRUTH plants {planted}"
    )


def test_the_results_table_states_the_same_number_of_plants() -> None:
    """The two places the count appears must agree, which is what failed before."""
    planted = sum(1 for cause in fixture.TRUTH.values() if cause)
    stable = sum(1 for cause in fixture.TRUTH.values() if cause is None)
    text = README.read_text(encoding="utf-8")
    claim = f"**{planted} flaky tests, one per cause the tool can name, and {stable} stable ones**"
    assert claim in text, (
        f"the Results section does not state {planted} flaky and {stable} stable tests, "
        f"which is what fixture.TRUTH holds"
    )


def test_every_planted_cause_is_distinct() -> None:
    """One plant per cause, which is the claim the Results section makes."""
    causes = [c for c in fixture.TRUTH.values() if c]
    assert len(causes) == len(set(causes)), (
        f"a cause is planted twice, so 'one per cause' is wrong: {sorted(causes)}"
    )
