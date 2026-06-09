"""Spec 13E.1 — Lurkr threat scan: secret / world_writable / eval_in_tool.

Three session-start blocking categories over the worktree, plus a
``.harness/lurkr-ignore.toml`` path/code suppression allowlist. Reuses the
``Finding`` type so ``has_blocking`` works over the combined session-start set.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dream.config.paths import DreamPaths
from dream.services.threat_scan import LurkrIgnore, load_lurkr_ignore, threat_scan

# AKIA + 16 upper/digit chars → matches the aws_access_key pattern.
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def _paths(tmp_path: Path) -> DreamPaths:
    return DreamPaths.resolve(tmp_path, env={})


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _codes(findings: list) -> set[str]:
    return {f.code for f in findings}


# --- secret ---------------------------------------------------------------


def test_secret_detected_in_src_and_redacted(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", f'KEY = "{FAKE_AWS}"\n')
    findings = threat_scan(_paths(tmp_path))
    secrets = [f for f in findings if f.code == "secret"]
    assert secrets
    assert FAKE_AWS not in secrets[0].message


def test_secret_detected_in_root_dotenv(tmp_path: Path) -> None:
    _write(tmp_path, ".env", f"AWS={FAKE_AWS}\n")
    assert "secret" in _codes(threat_scan(_paths(tmp_path)))


def test_noise_dirs_are_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "node_modules/pkg/x.js", f"const k = '{FAKE_AWS}'\n")
    _write(tmp_path, ".venv/lib/y.py", f"k = '{FAKE_AWS}'\n")
    assert "secret" not in _codes(threat_scan(_paths(tmp_path)))


def test_clean_repo_has_no_secret(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", "x = 1\n")
    assert "secret" not in _codes(threat_scan(_paths(tmp_path)))


def test_secret_detected_in_pem_file(tmp_path: Path) -> None:
    _write(tmp_path, "deploy/server.pem", f"{FAKE_AWS}\n")
    assert "secret" in _codes(threat_scan(_paths(tmp_path)))


def test_secret_detected_in_extensionless_credentials_file(tmp_path: Path) -> None:
    _write(tmp_path, "credentials", f"aws_key={FAKE_AWS}\n")
    assert "secret" in _codes(threat_scan(_paths(tmp_path)))


# --- world_writable -------------------------------------------------------

# ``chmod(0o666)`` does not reliably set the world-writable bit on non-POSIX
# platforms (notably Windows), so the permission-bit assertions are gated.
_posix_only = pytest.mark.skipif(
    os.name != "posix", reason="world-writable bit requires POSIX chmod semantics"
)


@_posix_only
def test_world_writable_under_docs_flagged(tmp_path: Path) -> None:
    p = _write(tmp_path, "docs/note.md", "hi\n")
    p.chmod(0o666)
    assert "world_writable" in _codes(threat_scan(_paths(tmp_path)))


@_posix_only
def test_normal_docs_file_not_flagged(tmp_path: Path) -> None:
    p = _write(tmp_path, "docs/note.md", "hi\n")
    p.chmod(0o644)
    assert "world_writable" not in _codes(threat_scan(_paths(tmp_path)))


# --- eval_in_tool ---------------------------------------------------------


def test_eval_in_repo_local_tool_flagged(tmp_path: Path) -> None:
    _write(tmp_path, ".harness/tools/evil.py", "def run():\n    return eval('1+1')\n")
    assert "eval_in_tool" in _codes(threat_scan(_paths(tmp_path)))


def test_subprocess_in_repo_local_tool_flagged(tmp_path: Path) -> None:
    _write(tmp_path, ".harness/tools/evil.py", "import subprocess\n")
    assert "eval_in_tool" in _codes(threat_scan(_paths(tmp_path)))


def test_clean_repo_local_tool_not_flagged(tmp_path: Path) -> None:
    _write(tmp_path, ".harness/tools/ok.py", "def run():\n    return 1 + 1\n")
    assert "eval_in_tool" not in _codes(threat_scan(_paths(tmp_path)))


def test_eval_in_string_or_comment_not_matched(tmp_path: Path) -> None:
    # AST-based: the word eval inside a string / comment must NOT match.
    _write(tmp_path, ".harness/tools/ok.py", "x = 'do not eval this'  # eval/subprocess\n")
    assert "eval_in_tool" not in _codes(threat_scan(_paths(tmp_path)))


def test_builtin_tools_not_scanned_for_eval(tmp_path: Path) -> None:
    # The harness's own tools live under src/ and legitimately use subprocess.
    _write(tmp_path, "src/dream/tools/builtin/bash.py", "import subprocess\n")
    assert "eval_in_tool" not in _codes(threat_scan(_paths(tmp_path)))


# --- suppression ----------------------------------------------------------


def test_ignore_glob_suppresses_secret(tmp_path: Path) -> None:
    _write(tmp_path, "tests/fixtures/sample.txt", f"key={FAKE_AWS}\n")
    _write(tmp_path, ".harness/lurkr-ignore.toml", 'paths = ["tests/fixtures/**"]\n')
    assert "secret" not in _codes(threat_scan(_paths(tmp_path)))


def test_ignore_code_suppresses_whole_category(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", f'k = "{FAKE_AWS}"\n')
    _write(tmp_path, ".harness/lurkr-ignore.toml", 'codes = ["secret"]\n')
    assert "secret" not in _codes(threat_scan(_paths(tmp_path)))


def test_load_ignore_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_lurkr_ignore(_paths(tmp_path)) == LurkrIgnore()


def test_ignore_glob_matches_backslash_path() -> None:
    # A finding path built from a Windows-style ``str(Path)`` is backslash-
    # separated; a POSIX-style ignore glob must still suppress it.
    ignore = LurkrIgnore(paths=("tests/fixtures/**",))
    assert ignore.suppresses("secret", "tests\\fixtures\\sample.txt")


def test_malformed_ignore_file_becomes_blocking_finding(tmp_path: Path) -> None:
    # A broken lurkr-ignore.toml must not crash session start; it surfaces as a
    # blocking finding instead of bubbling LurkrIgnoreError.
    _write(tmp_path, ".harness/lurkr-ignore.toml", "paths = [unclosed\n")
    findings = threat_scan(_paths(tmp_path))
    assert any(f.code == "lurkr_ignore_invalid" for f in findings)
    assert all(f.severity == "blocking" for f in findings)


def test_all_findings_are_blocking(tmp_path: Path) -> None:
    _write(tmp_path, "src/app.py", f'k = "{FAKE_AWS}"\n')
    findings = threat_scan(_paths(tmp_path))
    assert findings
    assert all(f.severity == "blocking" for f in findings)
