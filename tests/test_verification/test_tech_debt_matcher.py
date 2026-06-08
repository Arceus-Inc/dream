"""Spec 12e — operator-declared tech-debt matchers + failure matching."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.verification._tech_debt import (
    Matcher,
    TechDebtMatcherError,
    load_matchers,
    match_failure,
    parse_matchers,
)
from dream.verification._types import RepoVerificationStep


def _step(*, stderr: str = "", stdout: str = "", command: str = "pytest") -> RepoVerificationStep:
    return RepoVerificationStep(
        command=command, status="failed", returncode=1, stdout=stdout, stderr=stderr
    )


_CONFIG = """
[[matcher]]
pattern = "ModuleNotFoundError: No module named '([\\\\w.]+)'"
missing = "python module not installed: {1}"

[[matcher]]
pattern = "fixture '([\\\\w-]+)' not found"
missing = "missing test fixture: {1}"
"""


def _matchers() -> list[Matcher]:
    return parse_matchers(_CONFIG)


# --- config -----------------------------------------------------------------


def test_parse_matchers() -> None:
    matchers = parse_matchers(_CONFIG)
    assert len(matchers) == 2


def test_parse_empty_is_empty() -> None:
    assert parse_matchers("") == []


def test_parse_rejects_invalid_regex() -> None:
    with pytest.raises(TechDebtMatcherError):
        parse_matchers('[[matcher]]\npattern = "("\nmissing = "x"\n')


def test_parse_rejects_missing_fields() -> None:
    with pytest.raises(TechDebtMatcherError):
        parse_matchers('[[matcher]]\npattern = "x"\n')  # no `missing`


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_matchers(tmp_path / "none.toml") == []


def test_matchers_config_path(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path)
    assert paths.tech_debt_matchers() == tmp_path / ".harness" / "tech-debt-matchers.toml"


# --- match_failure ----------------------------------------------------------


def test_match_interpolates_capture_group() -> None:
    step = _step(stderr="ModuleNotFoundError: No module named 'httpx'")
    assert match_failure(step, _matchers()) == "python module not installed: httpx"


def test_match_searches_stdout_too() -> None:
    step = _step(stdout="fixture 'db-conn' not found", stderr="")
    assert match_failure(step, _matchers()) == "missing test fixture: db-conn"


def test_no_match_returns_none() -> None:
    assert match_failure(_step(stderr="some opaque traceback"), _matchers()) is None


def test_empty_matchers_never_match() -> None:
    assert match_failure(_step(stderr="ModuleNotFoundError: No module named 'x'"), []) is None


def test_first_matching_rule_wins() -> None:
    cfg = """
    [[matcher]]
    pattern = "boom"
    missing = "first"
    [[matcher]]
    pattern = "boom"
    missing = "second"
    """
    assert match_failure(_step(stderr="boom"), parse_matchers(cfg)) == "first"
