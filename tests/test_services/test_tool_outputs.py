"""Spec 04 stage 4a — tool-output offload to disk + microcompact eligibility.

Two pure-ish surfaces:

- ``is_microcompactable_tool_result`` — the *predicate* the compactor uses to
  decide whether an old ``ToolResultBlock``'s content can be dropped (the
  content goes; the structural shell stays so the tool-call atom — Spec 00
  invariant #1 — is never broken).
- ``offload_tool_output`` / ``OffloadPointer`` / ``read_offloaded`` — the
  *mechanism* by which a single oversized tool result is written to
  sidecar scratch and only a typed pointer (plus a head preview) is
  injected into context. Spec 04 acceptance #11.

The inline/preview/microcompact char thresholds are read from environment
each call so an operator can tune them without restarting the harness.
Defaults come from new-spec 04 (4 KB inline limit).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from dream.services.tool_outputs import (
    DEFAULT_MICROCOMPACT_TOOL_RESULT_CHARS,
    DEFAULT_TOOL_OUTPUT_INLINE_CHARS,
    DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS,
    OffloadPointer,
    is_microcompactable_tool_result,
    microcompact_tool_result_chars,
    offload_tool_output,
    read_offloaded,
    tool_output_inline_chars,
    tool_output_preview_chars,
)

# --- env-driven knobs --------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test starts with the relevant env vars unset so defaults apply."""
    for name in (
        "DREAM_TOOL_OUTPUT_INLINE_CHARS",
        "DREAM_TOOL_OUTPUT_PREVIEW_CHARS",
        "DREAM_MICROCOMPACT_TOOL_RESULT_CHARS",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


def test_tool_output_inline_chars_uses_default_when_unset() -> None:
    assert tool_output_inline_chars() == DEFAULT_TOOL_OUTPUT_INLINE_CHARS


def test_tool_output_inline_chars_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "12345")
    assert tool_output_inline_chars() == 12_345


def test_tool_output_inline_chars_clamps_below_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bogusly tiny inline limit MUST be clamped — otherwise every result spills."""
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "1")
    assert tool_output_inline_chars() >= 256


def test_tool_output_inline_chars_ignores_garbage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "not-an-int")
    assert tool_output_inline_chars() == DEFAULT_TOOL_OUTPUT_INLINE_CHARS


def test_tool_output_preview_chars_uses_default_when_unset() -> None:
    assert tool_output_preview_chars() == DEFAULT_TOOL_OUTPUT_PREVIEW_CHARS


def test_tool_output_preview_chars_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_PREVIEW_CHARS", "999")
    assert tool_output_preview_chars() == 999


def test_microcompact_tool_result_chars_uses_default_when_unset() -> None:
    assert microcompact_tool_result_chars() == DEFAULT_MICROCOMPACT_TOOL_RESULT_CHARS


# --- is_microcompactable_tool_result ----------------------------------------


def test_is_microcompactable_true_for_mcp_tools_regardless_of_size() -> None:
    """MCP tools are coarse-grained adapters — always droppable."""
    assert is_microcompactable_tool_result("mcp__github__list_prs", "tiny") is True


def test_is_microcompactable_true_for_large_results() -> None:
    """Anything at or above the configured size MUST be droppable."""
    content = "x" * (microcompact_tool_result_chars())
    assert is_microcompactable_tool_result("read_file", content) is True


def test_is_microcompactable_false_for_small_local_tool_result() -> None:
    """A tiny tool result from a non-MCP tool stays — dropping it saves nothing."""
    assert is_microcompactable_tool_result("read_file", "ok") is False


def test_is_microcompactable_ignores_tool_name_whitespace() -> None:
    assert is_microcompactable_tool_result("  mcp__x__y  ", "tiny") is True


# --- offload_tool_output / OffloadPointer -----------------------------------


def test_offload_under_limit_returns_content_unchanged(tmp_path: Path) -> None:
    """Spec 04 edge case: at-or-under-limit MUST stay inline (no pointer)."""
    payload = "small enough"
    inline, pointer = offload_tool_output(
        payload,
        scratch_dir=tmp_path,
        tool_use_id="tu_1",
        tool_name="read_file",
    )
    assert inline == payload
    assert pointer is None
    # And the offload directory MUST NOT have been touched for in-limit content.
    assert not any(tmp_path.iterdir())


def test_offload_at_exactly_limit_stays_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 04 edge case: a result *exactly* at the inline limit is inlined."""
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "1000")
    payload = "a" * 1000
    inline, pointer = offload_tool_output(
        payload,
        scratch_dir=tmp_path,
        tool_use_id="tu_1",
        tool_name="read_file",
    )
    assert inline == payload
    assert pointer is None


def test_offload_over_limit_spills_to_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "1000")
    payload = "a" * 10_000
    inline, pointer = offload_tool_output(
        payload,
        scratch_dir=tmp_path,
        tool_use_id="tu_42",
        tool_name="read_file",
    )
    assert pointer is not None
    assert isinstance(pointer, OffloadPointer)
    # The full payload landed on disk.
    artifact = tmp_path / pointer.offloaded_to
    assert artifact.exists()
    assert artifact.read_text(encoding="utf-8") == payload
    # And the inline text is *not* the full payload.
    assert payload not in inline


def test_offload_pointer_records_original_size_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "100")
    payload = "x" * 2_500
    _inline, pointer = offload_tool_output(
        payload,
        scratch_dir=tmp_path,
        tool_use_id="tu_1",
        tool_name="bash",
    )
    assert pointer is not None
    assert pointer.original_size_bytes == len(payload.encode("utf-8"))


def test_offload_pointer_records_head_preview_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "100")
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_PREVIEW_CHARS", "50")
    payload = "x" * 2_500
    _inline, pointer = offload_tool_output(
        payload,
        scratch_dir=tmp_path,
        tool_use_id="tu_1",
        tool_name="bash",
    )
    assert pointer is not None
    assert pointer.head_chars == 50


def test_offload_inline_text_carries_pointer_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The inline replacement MUST name the tool, the id, the artifact, and the size."""
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "100")
    payload = "x" * 2_500
    inline, pointer = offload_tool_output(
        payload,
        scratch_dir=tmp_path,
        tool_use_id="tu_42",
        tool_name="read_file",
    )
    assert pointer is not None
    assert "read_file" in inline
    assert "tu_42" in inline
    assert pointer.offloaded_to in inline
    assert str(pointer.original_size_bytes) in inline


def test_offload_uses_custom_summary_when_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "100")
    _inline, pointer = offload_tool_output(
        "x" * 2_500,
        scratch_dir=tmp_path,
        tool_use_id="tu_1",
        tool_name="bash",
        summary="grep -r foo across docs/",
    )
    assert pointer is not None
    assert pointer.summary == "grep -r foo across docs/"


def test_offload_creates_scratch_dir_if_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``scratch_dir`` may not yet exist; offload MUST create it."""
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "100")
    scratch = tmp_path / "deep" / "nested" / "scratch"
    _inline, pointer = offload_tool_output(
        "x" * 2_500,
        scratch_dir=scratch,
        tool_use_id="tu_1",
        tool_name="bash",
    )
    assert scratch.is_dir()
    assert pointer is not None
    assert (scratch / pointer.offloaded_to).exists()


def test_offload_artifact_paths_are_unique_for_repeat_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two offloads with the same tool/id MUST land in distinct files."""
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "100")
    _i1, p1 = offload_tool_output(
        "x" * 2_500,
        scratch_dir=tmp_path,
        tool_use_id="tu_dup",
        tool_name="bash",
    )
    _i2, p2 = offload_tool_output(
        "y" * 2_500,
        scratch_dir=tmp_path,
        tool_use_id="tu_dup",
        tool_name="bash",
    )
    assert p1 is not None and p2 is not None
    assert p1.offloaded_to != p2.offloaded_to


def test_offload_sanitises_tool_name_in_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path-traversal characters in the tool name MUST be stripped from the filename."""
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "100")
    _inline, pointer = offload_tool_output(
        "x" * 2_500,
        scratch_dir=tmp_path,
        tool_use_id="tu_1",
        tool_name="../weird/../name",
    )
    assert pointer is not None
    assert ".." not in pointer.offloaded_to
    assert "/" not in pointer.offloaded_to
    assert "\\" not in pointer.offloaded_to


# --- read_offloaded ----------------------------------------------------------


def test_read_offloaded_returns_full_content_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "10")
    payload = "abcdefghij" * 200
    _inline, pointer = offload_tool_output(
        payload,
        scratch_dir=tmp_path,
        tool_use_id="tu_1",
        tool_name="bash",
    )
    assert pointer is not None
    assert read_offloaded(tmp_path / pointer.offloaded_to) == payload


def test_read_offloaded_slices_with_start_and_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "10")
    payload = "abcdefghij" * 200  # 2000 chars
    _inline, pointer = offload_tool_output(
        payload,
        scratch_dir=tmp_path,
        tool_use_id="tu_1",
        tool_name="bash",
    )
    assert pointer is not None
    sliced = read_offloaded(tmp_path / pointer.offloaded_to, start=10, end=20)
    assert sliced == payload[10:20]


def test_read_offloaded_open_ended_slice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``end=None`` MUST read to end-of-file."""
    monkeypatch.setenv("DREAM_TOOL_OUTPUT_INLINE_CHARS", "10")
    payload = "abcdefghij" * 50
    _inline, pointer = offload_tool_output(
        payload,
        scratch_dir=tmp_path,
        tool_use_id="tu_1",
        tool_name="bash",
    )
    assert pointer is not None
    assert read_offloaded(tmp_path / pointer.offloaded_to, start=400) == payload[400:]


def test_read_offloaded_rejects_path_outside_scratch(tmp_path: Path) -> None:
    """Path traversal MUST be rejected — read_offloaded only opens scratch artifacts."""
    # Create a file outside the offload area and try to slip it through.
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("ssh-keys")
    with pytest.raises((ValueError, OSError)):
        read_offloaded(tmp_path / ".." / "secret.txt")


def test_read_offloaded_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_offloaded(tmp_path / "nope.txt")


def test_read_offloaded_root_rejects_absolute_escape(tmp_path: Path) -> None:
    """With ``root`` set, an absolute path outside it is rejected (no ``..`` needed)."""
    root = tmp_path / "scratch"
    root.mkdir()
    secret = tmp_path / "etc_passwd.txt"
    secret.write_text("root:x:0:0", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes the allowed root"):
        read_offloaded(secret, root=root)


def test_read_offloaded_root_rejects_symlink_escape(tmp_path: Path) -> None:
    """A symlink inside root pointing outside is caught after resolution."""
    root = tmp_path / "scratch"
    root.mkdir()
    secret = tmp_path / "outside.txt"
    secret.write_text("classified", encoding="utf-8")
    link = root / "link.txt"
    link.symlink_to(secret)
    with pytest.raises(ValueError, match="escapes the allowed root"):
        read_offloaded(link, root=root)


def test_read_offloaded_root_allows_contained_file(tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    (root / "ok.txt").write_text("inside", encoding="utf-8")
    assert read_offloaded(root / "ok.txt", root=root) == "inside"
