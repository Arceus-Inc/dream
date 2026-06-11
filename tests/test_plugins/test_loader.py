"""Plugin loading (spec 13 §extension surface 15-18, 22; spec 15 P4 §3).

Repo-local ``plugins/{name}/manifest.toml``, opt-in via
``.harness/plugins-enabled.toml``, capability-gated against the sandbox
tier, loaded under an init timeout. A plugin failure never aborts the
caller — it is reported and skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.permissions import SandboxTier
from dream.plugins import (
    PluginManifestError,
    load_enabled_plugins,
    parse_manifest,
    read_enabled_names,
)

_MANIFEST = """\
name        = "metrics-pusher"
version     = "0.2.0"
entry       = "main.py"
description = "Push session metrics."

[capabilities]
required = ["repo-write"]
"""

_ENTRY = '''\
from dream.contracts.plugin import Plugin


def get_plugin(manifest):
    return Plugin(manifest=manifest)
'''


def _write_plugin(
    repo: Path,
    name: str = "metrics-pusher",
    *,
    manifest: str = _MANIFEST,
    entry: str = _ENTRY,
) -> Path:
    plugin_dir = repo / "plugins" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    body = manifest.replace('"metrics-pusher"', f'"{name}"')
    (plugin_dir / "manifest.toml").write_text(body, encoding="utf-8")
    (plugin_dir / "main.py").write_text(entry, encoding="utf-8")
    return plugin_dir


def _enable(repo: Path, *names: str) -> None:
    enabled = repo / ".harness" / "plugins-enabled.toml"
    enabled.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f'[[plugin]]\nname = "{n}"\n' for n in names)
    enabled.write_text(body, encoding="utf-8")


# --- manifest parsing -------------------------------------------------------


def test_parse_manifest_round_trip() -> None:
    manifest = parse_manifest(_MANIFEST)
    assert manifest.name == "metrics-pusher"
    assert manifest.version == "0.2.0"
    assert manifest.entry == "main.py"
    assert manifest.metadata["capabilities"] == ("repo-write",)


@pytest.mark.parametrize(
    "broken",
    [
        "version = '1.0'\nentry = 'main.py'",  # missing name
        "name = 'x'\nentry = 'main.py'",  # missing version
        "name = 'x'\nversion = '1.0'",  # missing entry
        "name = '../evil'\nversion = '1'\nentry = 'main.py'",  # path traversal
        "this = = not toml",
    ],
)
def test_malformed_manifest_refused(broken: str) -> None:
    with pytest.raises(PluginManifestError):
        parse_manifest(broken)


def test_unknown_capability_refused() -> None:
    bad = _MANIFEST.replace('"repo-write"', '"root-access"')
    with pytest.raises(PluginManifestError, match="capability"):
        parse_manifest(bad)


# --- enablement -------------------------------------------------------------


def test_no_enabled_file_means_no_plugins(tmp_path: Path) -> None:
    _write_plugin(tmp_path)
    assert read_enabled_names(tmp_path) == ()
    report = load_enabled_plugins(tmp_path, tier=SandboxTier.REPO_WRITE)
    assert report.loaded == ()


def test_on_disk_but_not_listed_is_ignored(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "metrics-pusher")
    _write_plugin(tmp_path, "other")
    _enable(tmp_path, "other")
    report = load_enabled_plugins(tmp_path, tier=SandboxTier.REPO_WRITE)
    assert [p.manifest.name for p in report.loaded] == ["other"]


# --- loading ----------------------------------------------------------------


def test_load_happy_path(tmp_path: Path) -> None:
    _write_plugin(tmp_path)
    _enable(tmp_path, "metrics-pusher")
    report = load_enabled_plugins(tmp_path, tier=SandboxTier.REPO_WRITE)
    assert [p.manifest.name for p in report.loaded] == ["metrics-pusher"]
    assert report.failed == ()


def test_capability_exceeding_tier_refused(tmp_path: Path) -> None:
    manifest = _MANIFEST.replace('"repo-write"', '"network"')
    _write_plugin(tmp_path, manifest=manifest)
    _enable(tmp_path, "metrics-pusher")
    report = load_enabled_plugins(tmp_path, tier=SandboxTier.REPO_WRITE)
    assert report.loaded == ()
    assert len(report.failed) == 1
    assert "network" in report.failed[0].reason


def test_crashing_entry_is_reported_not_raised(tmp_path: Path) -> None:
    _write_plugin(tmp_path, entry="raise RuntimeError('boom at import')\n")
    _enable(tmp_path, "metrics-pusher")
    report = load_enabled_plugins(tmp_path, tier=SandboxTier.REPO_WRITE)
    assert report.loaded == ()
    assert "boom at import" in report.failed[0].reason


def test_missing_plugin_dir_is_reported(tmp_path: Path) -> None:
    _enable(tmp_path, "ghost")
    report = load_enabled_plugins(tmp_path, tier=SandboxTier.REPO_WRITE)
    assert report.loaded == ()
    assert report.failed[0].name == "ghost"


def test_entry_without_get_plugin_is_reported(tmp_path: Path) -> None:
    _write_plugin(tmp_path, entry="x = 1\n")
    _enable(tmp_path, "metrics-pusher")
    report = load_enabled_plugins(tmp_path, tier=SandboxTier.REPO_WRITE)
    assert "get_plugin" in report.failed[0].reason


def test_version_pin_mismatch_warns_not_fails(tmp_path: Path) -> None:
    _write_plugin(tmp_path)
    enabled = tmp_path / ".harness" / "plugins-enabled.toml"
    enabled.parent.mkdir(parents=True, exist_ok=True)
    enabled.write_text(
        '[[plugin]]\nname = "metrics-pusher"\nversion = "9.9.9"\n', encoding="utf-8"
    )
    report = load_enabled_plugins(tmp_path, tier=SandboxTier.REPO_WRITE)
    assert [p.manifest.name for p in report.loaded] == ["metrics-pusher"]
    assert any("version" in w for w in report.warnings)
