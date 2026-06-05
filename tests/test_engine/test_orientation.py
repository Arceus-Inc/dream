"""Spec 03 stage 3b — orientation ritual.

Pins the orientation primitives + their composition:

- ``ValidatorFinding`` carries severity/code/message/path.
- ``OrientationBrief`` is a frozen, structured record that the orchestrator
  will inject into the first turn (#15 — first turn always sees the
  validator findings) and that exposes ``has_blocking_findings`` so the
  session can refuse ``orienting -> working`` when a blocking finding is
  present.
- ``run_orientation(config)`` runs the hybrid ritual: a deterministic
  gather step then an optional LLM summary step. ``--no-ai-orientation``
  is encoded by leaving ``OrientationConfig.summariser`` as ``None``
  (no summariser instance is constructed at all in that mode).
- A summariser exception is best-effort: orientation succeeds with
  ``llm_summary=None`` rather than failing the session.
- ``to_user_message`` renders the brief into a ``ConversationMessage``
  the orchestrator can prepend to the transcript.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from dream.engine._messages import ConversationMessage, TextBlock
from dream.engine._orientation import (
    OrientationBrief,
    OrientationConfig,
    ValidatorFinding,
    run_orientation,
)


def _brief(**overrides: object) -> OrientationBrief:
    defaults: dict[str, object] = dict(
        repo_summary="dream repo, two crates",
        progress_tail="last entry: spec 03 stage 3a merged",
        active_exec_plan="spec 03 stage 3b",
        validator_findings=[],
        core_beliefs_digest=["tool-call atom is sacred"],
        house_rules=["no logging in src"],
    )
    defaults.update(overrides)
    return OrientationBrief(**defaults)  # type: ignore[arg-type]


# --- ValidatorFinding -------------------------------------------------------


def test_validator_finding_holds_severity_code_message_and_optional_path() -> None:
    f = ValidatorFinding(
        severity="warning", code="V-001", message="readme stale", path="README.md"
    )
    assert f.severity == "warning"
    assert f.code == "V-001"
    assert f.message == "readme stale"
    assert f.path == "README.md"


def test_validator_finding_path_defaults_to_none() -> None:
    f = ValidatorFinding(severity="info", code="V-002", message="hint")
    assert f.path is None


def test_validator_finding_is_frozen() -> None:
    f = ValidatorFinding(severity="info", code="V-002", message="hint")
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(f, "code", "V-099")


# --- OrientationBrief shape -------------------------------------------------


def test_brief_is_frozen() -> None:
    b = _brief()
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        setattr(b, "repo_summary", "mutated")


def test_brief_defaults_are_empty_and_llm_summary_starts_none() -> None:
    b = OrientationBrief(
        repo_summary="", progress_tail="", active_exec_plan=""
    )
    assert b.validator_findings == []
    assert b.core_beliefs_digest == []
    assert b.house_rules == []
    assert b.llm_summary is None


def test_brief_has_blocking_findings_true_only_when_severity_blocking() -> None:
    b_clean = _brief()
    assert b_clean.has_blocking_findings is False

    b_warn = _brief(
        validator_findings=[ValidatorFinding("warning", "V-1", "msg")]
    )
    assert b_warn.has_blocking_findings is False

    b_block = _brief(
        validator_findings=[
            ValidatorFinding("warning", "V-1", "msg"),
            ValidatorFinding("blocking", "V-2", "boom"),
        ]
    )
    assert b_block.has_blocking_findings is True


# --- to_user_message --------------------------------------------------------


def test_to_user_message_returns_user_role_with_textblock() -> None:
    b = _brief()
    msg = b.to_user_message()
    assert isinstance(msg, ConversationMessage)
    assert msg.role == "user"
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], TextBlock)


def test_to_user_message_text_carries_all_sections_findings_beliefs_rules() -> None:
    b = _brief(
        repo_summary="dream",
        progress_tail="stage 3a merged",
        active_exec_plan="stage 3b",
        validator_findings=[
            ValidatorFinding("warning", "V-001", "stale readme", "README.md"),
            ValidatorFinding("info", "V-002", "consider tagging"),
        ],
        core_beliefs_digest=["atom is sacred"],
        house_rules=["no logging in src"],
    )
    text = b.to_user_message().text
    # All sections present
    assert "dream" in text
    assert "stage 3a merged" in text
    assert "stage 3b" in text
    # Validator findings — code + severity + message + optional path
    assert "V-001" in text
    assert "warning" in text
    assert "stale readme" in text
    assert "README.md" in text
    assert "V-002" in text
    # Beliefs and rules carried verbatim
    assert "atom is sacred" in text
    assert "no logging in src" in text


def test_to_user_message_includes_llm_summary_when_present() -> None:
    b = _brief(llm_summary="One paragraph from the summariser.")
    assert "One paragraph from the summariser." in b.to_user_message().text


def test_to_user_message_omits_summary_marker_when_llm_summary_is_none() -> None:
    b = _brief(llm_summary=None)
    text = b.to_user_message().text
    # If the section header leaks when summary is None it suggests the
    # renderer is unconditionally emitting the block.
    assert "None" not in text


# --- run_orientation --------------------------------------------------------


async def test_run_orientation_returns_gather_result_when_no_summariser() -> None:
    expected = _brief(repo_summary="from gather")

    async def gather() -> OrientationBrief:
        return expected

    out = await run_orientation(OrientationConfig(gather=gather))
    assert out is expected or out == expected
    assert out.llm_summary is None


async def test_run_orientation_calls_summariser_and_populates_llm_summary() -> None:
    gathered = _brief(repo_summary="r")
    seen: list[OrientationBrief] = []

    async def gather() -> OrientationBrief:
        return gathered

    async def summarise(b: OrientationBrief) -> str:
        seen.append(b)
        return "the summary"

    out = await run_orientation(
        OrientationConfig(gather=gather, summariser=summarise)
    )
    assert seen == [gathered]
    assert out.llm_summary == "the summary"
    # Other fields preserved (we copy, not replace, the brief)
    assert out.repo_summary == "r"


async def test_run_orientation_gather_is_awaited_exactly_once() -> None:
    calls = 0

    async def gather() -> OrientationBrief:
        nonlocal calls
        calls += 1
        return _brief()

    await run_orientation(OrientationConfig(gather=gather))
    assert calls == 1


async def test_run_orientation_summariser_exception_is_swallowed_best_effort() -> None:
    async def gather() -> OrientationBrief:
        return _brief(repo_summary="g")

    async def boom(_: OrientationBrief) -> str:
        raise RuntimeError("model down")

    out = await run_orientation(
        OrientationConfig(gather=gather, summariser=boom)
    )
    assert out.llm_summary is None
    assert out.repo_summary == "g"


async def test_run_orientation_skips_summariser_when_brief_has_blocking_findings() -> None:
    """A blocking finding means the session will abort before orienting
    completes — paying an LLM round-trip for a summary nobody will read
    is wasteful, so the ritual short-circuits.
    """
    summariser_called = False

    async def gather() -> OrientationBrief:
        return _brief(
            validator_findings=[
                ValidatorFinding("blocking", "V-9", "stop")
            ]
        )

    async def summarise(_: OrientationBrief) -> str:
        nonlocal summariser_called
        summariser_called = True
        return "summary"

    out = await run_orientation(
        OrientationConfig(gather=gather, summariser=summarise)
    )
    assert summariser_called is False
    assert out.llm_summary is None
    assert out.has_blocking_findings is True
