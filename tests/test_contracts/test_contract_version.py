"""Pin the cross-repo contract version.

``dream.contracts.__contract_version__`` is the single coordination point
across the four repos (dream, chorus, lattice, horizon). Siblings assert the
running dream's contract version is within the range they were built against
and fail fast otherwise (chorus spec 05 §2). It follows semver: a breaking
Protocol change is a dream MAJOR bump.
"""

from __future__ import annotations

import re

import dream.contracts as contracts

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_contract_version_is_a_semver_string() -> None:
    version = contracts.__contract_version__
    assert isinstance(version, str)
    assert _SEMVER.match(version), f"not semver: {version!r}"


def test_contract_version_in_all() -> None:
    assert "__contract_version__" in contracts.__all__
