"""
The deployed file is the committed file.

The contract is tested through its bundle, so a bundle that has drifted
from the modules would make every other test in this suite a test of
something that is not in the repo, and a deploy would put code on chain that
nobody reviewed. Regenerating here and comparing is the cheapest way to make
that impossible to do by accident:

    python -m deploy.build_bundle
"""

from pathlib import Path

import pytest

from deploy.build_bundle import BUNDLES, build

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


@pytest.mark.parametrize("target", sorted(BUNDLES))
def test_the_bundle_in_the_repo_matches_its_sources(target):
    on_disk = (CONTRACTS / target).read_text(encoding="utf-8")
    assert on_disk == build(BUNDLES[target]), f"{target} is stale: run python -m deploy.build_bundle"


@pytest.mark.parametrize("target", sorted(BUNDLES))
def test_the_runner_header_is_the_first_two_lines_and_appears_once(target):
    """
    GenVM reads the leading comment block as the runner header. A second copy
    of it further down, or a comment on line two, fails the deploy outright.
    """
    lines = (CONTRACTS / target).read_text(encoding="utf-8").split("\n")
    assert lines[0] == "# v0.3.0"
    assert lines[1].startswith('# { "Depends": "py-genlayer:')
    assert not lines[2].startswith("#")
    assert len([ln for ln in lines if ln.startswith("# v0.3.0")]) == 1


@pytest.mark.parametrize("target", sorted(BUNDLES))
def test_the_bundle_has_no_import_of_a_sibling_module(target):
    """GenVM loads one file with no siblings, so a surviving import is a deploy that dies on first call."""
    assert "from contracts." not in (CONTRACTS / target).read_text(encoding="utf-8")


@pytest.mark.parametrize("target", sorted(BUNDLES))
def test_the_bundle_is_well_under_the_gas_cap(target):
    """
    A deploy over the gas cap gets a transaction hash and then does not exist,
    with no error anywhere, so size is worth asserting rather than noticing.
    """
    assert (CONTRACTS / target).stat().st_size < 48_000
