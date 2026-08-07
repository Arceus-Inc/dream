"""Component tests for failover wire: pool → slots → streamer assembly."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.engine._failover_wire import (
    StreamerParts,
    single_key_pool,
    slot_from_pool,
    slots_for_session,
)
from dream.engine._loop import TurnStreamer


class _StubStreamer:
    async def stream_turn(self, messages: object) -> object:
        if False:  # pragma: no cover
            yield messages


def _parts() -> StreamerParts:
    return StreamerParts(
        model="gpt-test",
        base_url="https://example.test/v1",
        system_prompt="sys",
        extra_params=None,
    )


def test_single_key_pool_labels_env_not_the_secret() -> None:
    pool = single_key_pool(substrate="primary", label="env", api_key="sk-secret")
    cred = pool.pick_live()
    assert cred.label == "env"
    assert cred.key == "sk-secret"
    assert "sk-secret" not in repr(cred)


def test_slot_from_pool_builds_one_streamer_per_label() -> None:
    pool = single_key_pool(substrate="primary", label="env", api_key="sk-1")
    built: list[str] = []

    def _make(api_key: str, parts: StreamerParts) -> TurnStreamer:
        built.append(api_key)
        return _StubStreamer()  # type: ignore[return-value]

    slot = slot_from_pool(pool=pool, parts=_parts(), make_streamer=_make)
    assert slot.name == "primary"
    assert frozenset(slot.streamers) == frozenset({"env"})
    assert built == ["sk-1"]


def test_slots_for_session_reads_credentials_toml(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    creds = harness / "credentials.toml"
    creds.write_text(
        '[[primary]]\nkey = "sk-a"\nlabel = "a"\n\n[[primary]]\nkey = "sk-b"\nlabel = "b"\n',
        encoding="utf-8",
    )
    creds.chmod(0o600)

    def _make(api_key: str, parts: StreamerParts) -> TurnStreamer:
        return _StubStreamer()  # type: ignore[return-value]

    # Patch via slot_from_pool by building through slots_for_session then
    # re-checking labels — slots_for_session uses openai_streamer_for_key by
    # default which needs network config only at call time; we only inspect pool.
    slots = slots_for_session(
        api_key="unused",
        parts=_parts(),
        credentials_path=creds,
        active_substrate="primary",
    )
    assert len(slots) == 1
    labels = {c.label for c in slots[0].pool.all_credentials()}
    assert labels == {"a", "b"}


def test_slots_for_session_accepts_openai_without_primary(tmp_path: Path) -> None:
    """Factory must not require an unrelated ``primary`` pool in credentials.toml."""
    harness = tmp_path / ".harness"
    harness.mkdir()
    creds = harness / "credentials.toml"
    creds.write_text(
        '[[openai]]\nkey = "sk-oai"\nlabel = "main"\n',
        encoding="utf-8",
    )
    creds.chmod(0o600)

    slots = slots_for_session(
        api_key="unused",
        parts=_parts(),
        credentials_path=creds,
    )
    assert len(slots) == 1
    assert slots[0].name == "openai"
    assert slots[0].pool.pick_live().key == "sk-oai"


def test_slots_for_session_falls_back_to_env_key(tmp_path: Path) -> None:
    slots = slots_for_session(
        api_key="sk-env",
        parts=_parts(),
        credentials_path=tmp_path / "missing.toml",
    )
    assert len(slots) == 1
    assert slots[0].pool.pick_live().key == "sk-env"
    assert slots[0].name == "primary"


def test_empty_api_key_rejected() -> None:
    with pytest.raises(ValueError, match="api_key"):
        single_key_pool(substrate="primary", label="env", api_key="")
