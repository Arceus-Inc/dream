"""Spec 13A — non-disableable credential-path guard.

Matches user/system secret locations + the harness's own credential store.
Operator ``extra`` patterns ADD to the guard; they never shrink the built-ins.
Repo-local ``.env`` / ``*.key`` are deliberately excluded from the built-ins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.permissions._credential_guard import (
    BUILTIN_CREDENTIAL_PATTERNS,
    is_credential_path,
)


def test_ssh_private_key_is_credential(tmp_path: Path) -> None:
    assert is_credential_path(Path.home() / ".ssh" / "id_rsa", tmp_path)


def test_ssh_directory_itself_is_credential(tmp_path: Path) -> None:
    assert is_credential_path(Path.home() / ".ssh", tmp_path)


def test_aws_credentials_is_credential(tmp_path: Path) -> None:
    assert is_credential_path(Path.home() / ".aws" / "credentials", tmp_path)


def test_netrc_is_credential(tmp_path: Path) -> None:
    assert is_credential_path(Path.home() / ".netrc", tmp_path)


def test_gh_hosts_is_credential_but_other_gh_files_are_not(tmp_path: Path) -> None:
    assert is_credential_path(Path.home() / ".config" / "gh" / "hosts.yml", tmp_path)
    assert not is_credential_path(Path.home() / ".config" / "gh" / "notes.txt", tmp_path)


def test_pem_anywhere_is_credential(tmp_path: Path) -> None:
    assert is_credential_path(tmp_path / "certs" / "server.pem", tmp_path)


def test_id_rsa_in_repo_is_credential(tmp_path: Path) -> None:
    assert is_credential_path(tmp_path / "id_rsa", tmp_path)


def test_harness_credential_store_is_credential(tmp_path: Path) -> None:
    assert is_credential_path(tmp_path / ".harness" / "mcp-credentials.toml", tmp_path)


def test_relative_path_is_resolved_against_cwd(tmp_path: Path) -> None:
    assert is_credential_path(Path(".harness/mcp-credentials.toml"), tmp_path)


def test_ordinary_repo_file_is_not_credential(tmp_path: Path) -> None:
    assert not is_credential_path(tmp_path / "src" / "main.py", tmp_path)


def test_repo_local_dotenv_excluded_from_builtins(tmp_path: Path) -> None:
    assert not is_credential_path(tmp_path / ".env", tmp_path)


def test_repo_local_key_excluded_from_builtins(tmp_path: Path) -> None:
    assert not is_credential_path(tmp_path / "server.key", tmp_path)


def test_operator_extra_pattern_extends_guard(tmp_path: Path) -> None:
    assert not is_credential_path(tmp_path / "secret.vault", tmp_path)
    assert is_credential_path(tmp_path / "secret.vault", tmp_path, ("**/*.vault",))


def test_extras_do_not_shrink_builtins(tmp_path: Path) -> None:
    assert is_credential_path(Path.home() / ".ssh" / "id_rsa", tmp_path, ("**/*.vault",))


def test_symlink_into_credential_dir_is_caught(tmp_path: Path) -> None:
    # A repo-local symlink pointing at a real credential file is blocked via
    # the resolved form, even though the link's own path looks innocuous.
    target = Path.home() / ".ssh" / "id_rsa"
    link = tmp_path / "innocent.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:  # restricted runners (e.g. Windows CI) can't symlink
        pytest.skip(f"symlink creation unsupported here: {exc}")
    assert is_credential_path(link, tmp_path)


def test_builtins_nonempty_and_include_harness_store() -> None:
    assert BUILTIN_CREDENTIAL_PATTERNS
    assert any("mcp-credentials.toml" in p for p in BUILTIN_CREDENTIAL_PATTERNS)
