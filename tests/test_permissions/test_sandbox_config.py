"""Spec 13B — sandbox.toml loader + the unrestricted double-confirm gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.permissions._config import (
    SandboxConfig,
    SandboxConfigError,
    parse_sandbox_config,
    read_sandbox_config,
)
from dream.permissions._types import SandboxTier


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    cfg = read_sandbox_config(tmp_path / "absent.toml")
    assert cfg == SandboxConfig()
    assert cfg.tier is SandboxTier.REPO_WRITE
    assert cfg.extra_allowed == ()
    assert cfg.credential_extra == ()


def test_empty_text_defaults_to_repo_write() -> None:
    assert parse_sandbox_config("").tier is SandboxTier.REPO_WRITE


def test_parse_tier_and_extras() -> None:
    cfg = parse_sandbox_config(
        'tier = "read-only"\n'
        'extra_allowed = ["../shared", "/tmp/x"]\n'
        'credential_extra = ["**/*.vault"]\n'
    )
    assert cfg.tier is SandboxTier.READ_ONLY
    assert cfg.extra_allowed == ("../shared", "/tmp/x")
    assert cfg.credential_extra == ("**/*.vault",)


def test_unknown_tier_string_raises() -> None:
    with pytest.raises(SandboxConfigError):
        parse_sandbox_config('tier = "god-mode"')


def test_unrestricted_without_confirm_raises() -> None:
    with pytest.raises(SandboxConfigError):
        parse_sandbox_config('tier = "unrestricted"')


def test_unrestricted_with_confirm_ok() -> None:
    cfg = parse_sandbox_config('tier = "unrestricted"\nconfirm_unrestricted = true\n')
    assert cfg.tier is SandboxTier.UNRESTRICTED


def test_confirm_without_unrestricted_is_ignored() -> None:
    cfg = parse_sandbox_config('tier = "repo-write"\nconfirm_unrestricted = true\n')
    assert cfg.tier is SandboxTier.REPO_WRITE


def test_malformed_toml_raises() -> None:
    with pytest.raises(SandboxConfigError):
        parse_sandbox_config("tier = = =")


def test_non_string_extra_allowed_raises() -> None:
    with pytest.raises(SandboxConfigError):
        parse_sandbox_config("extra_allowed = [1, 2]")


def test_non_list_credential_extra_raises() -> None:
    with pytest.raises(SandboxConfigError):
        parse_sandbox_config('credential_extra = "oops"')


def test_read_parses_real_file(tmp_path: Path) -> None:
    f = tmp_path / "sandbox.toml"
    f.write_text('tier = "repo-write+net-allowlist"\n', encoding="utf-8")
    assert read_sandbox_config(f).tier is SandboxTier.REPO_WRITE_NET
