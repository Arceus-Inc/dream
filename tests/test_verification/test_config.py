"""Spec 12c — verification step config (.harness/verification.toml) + report path."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.verification._config import (
    VerificationConfigError,
    parse_verification_config,
    read_verification_config,
)
from dream.verification._types import VerificationStepSpec


def test_parse_steps_with_names() -> None:
    text = """
    [[step]]
    name = "unit tests"
    command = "pytest -q"

    [[step]]
    command = "ruff check src"
    """
    steps = parse_verification_config(text)
    assert steps == [
        VerificationStepSpec(command="pytest -q", name="unit tests"),
        VerificationStepSpec(command="ruff check src"),
    ]


def test_parse_empty_is_empty() -> None:
    assert parse_verification_config("") == []


def test_parse_rejects_step_without_command() -> None:
    with pytest.raises(VerificationConfigError):
        parse_verification_config('[[step]]\nname = "x"\n')


def test_parse_rejects_malformed_toml() -> None:
    with pytest.raises(VerificationConfigError):
        parse_verification_config("not = = toml")


def test_read_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_verification_config(tmp_path / "nope.toml") == []


def test_read_directory_path_raises(tmp_path: Path) -> None:
    # An existing-but-non-file path (a directory here) is misconfiguration and
    # must fail fast, not silently skip verification.
    config_dir = tmp_path / "verification.toml"
    config_dir.mkdir()
    with pytest.raises(VerificationConfigError):
        read_verification_config(config_dir)


def test_read_parses_file(tmp_path: Path) -> None:
    path = tmp_path / "verification.toml"
    path.write_text('[[step]]\ncommand = "pytest -q"\n', encoding="utf-8")
    assert read_verification_config(path) == [VerificationStepSpec(command="pytest -q")]


def test_verification_report_path(tmp_path: Path) -> None:
    paths = DreamPaths.resolve(tmp_path)
    expected = paths.sidecar("T1") / "metrics" / "verification-report.json"
    assert paths.verification_report("T1") == expected
