"""Spec 06.5 slice 2 — typed ``WakeSource`` discriminated union.

The wake source is what fired this background turn. The spec pins three
v1 kinds (``cron``, ``idle_timer``, ``inbound_message``) plus an
out-of-band ``manual`` kind for the REPL ``/wake`` slash command. Each
variant carries its own typed payload so the on-disk jsonl record matches
the spec's example shape (``{"kind": "cron", "cron_kind": "doc-garden"}``).
"""

from __future__ import annotations

import pytest

from dream.wake import WakeSource
from dream.wake._source import (
    CronWake,
    IdleTimerWake,
    InboundMessageWake,
    ManualWake,
    wake_source_from_dict,
    wake_source_to_dict,
)


def test_cron_wake_carries_cron_kind_and_optional_run_id() -> None:
    src = CronWake(cron_kind="doc-garden", run_id="r-1")
    assert src.kind == "cron"
    assert src.cron_kind == "doc-garden"
    assert src.run_id == "r-1"


def test_cron_wake_run_id_is_optional() -> None:
    src = CronWake(cron_kind="doc-garden")
    assert src.run_id is None


def test_idle_timer_wake_carries_minutes() -> None:
    src = IdleTimerWake(idle_minutes=47)
    assert src.kind == "idle_timer"
    assert src.idle_minutes == 47


def test_inbound_message_wake_carries_channel_and_ref() -> None:
    src = InboundMessageWake(channel="slack#ops", message_ref="m-9")
    assert src.kind == "inbound_message"
    assert src.channel == "slack#ops"
    assert src.message_ref == "m-9"


def test_manual_wake_has_no_payload() -> None:
    """``manual`` is the REPL ``/wake`` source — no useful metadata."""
    src = ManualWake()
    assert src.kind == "manual"


def test_wake_source_is_a_union_of_all_variants() -> None:
    """``WakeSource`` is the type alias used at API boundaries."""
    # Every variant is structurally a ``WakeSource`` — proven by the fact
    # that the helper functions take/return ``WakeSource`` and accept all.
    for src in (
        CronWake(cron_kind="x"),
        IdleTimerWake(idle_minutes=1),
        InboundMessageWake(channel="c", message_ref="r"),
        ManualWake(),
    ):
        assert isinstance(src, WakeSource)  # type: ignore[misc]


def test_wake_source_variants_are_frozen() -> None:
    src = CronWake(cron_kind="x")
    with pytest.raises((AttributeError, TypeError)):
        setattr(src, "cron_kind", "y")


# --- jsonl serialization ----------------------------------------------------


def test_cron_to_dict_includes_kind_and_payload() -> None:
    d = wake_source_to_dict(CronWake(cron_kind="doc-garden", run_id="r-1"))
    assert d == {"kind": "cron", "cron_kind": "doc-garden", "run_id": "r-1"}


def test_cron_to_dict_omits_unset_run_id() -> None:
    """Slice 2: omit ``run_id`` when ``None`` so the jsonl line stays tidy."""
    d = wake_source_to_dict(CronWake(cron_kind="doc-garden"))
    assert d == {"kind": "cron", "cron_kind": "doc-garden"}
    assert "run_id" not in d


def test_idle_timer_to_dict() -> None:
    d = wake_source_to_dict(IdleTimerWake(idle_minutes=47))
    assert d == {"kind": "idle_timer", "idle_minutes": 47}


def test_inbound_message_to_dict() -> None:
    d = wake_source_to_dict(InboundMessageWake(channel="c", message_ref="r"))
    assert d == {"kind": "inbound_message", "channel": "c", "message_ref": "r"}


def test_manual_to_dict() -> None:
    d = wake_source_to_dict(ManualWake())
    assert d == {"kind": "manual"}


def test_roundtrip_cron_with_run_id() -> None:
    src = CronWake(cron_kind="doc-garden", run_id="r-1")
    assert wake_source_from_dict(wake_source_to_dict(src)) == src


def test_roundtrip_cron_without_run_id() -> None:
    src = CronWake(cron_kind="doc-garden")
    assert wake_source_from_dict(wake_source_to_dict(src)) == src


def test_roundtrip_idle_timer() -> None:
    src = IdleTimerWake(idle_minutes=120)
    assert wake_source_from_dict(wake_source_to_dict(src)) == src


def test_roundtrip_inbound_message() -> None:
    src = InboundMessageWake(channel="c", message_ref="r")
    assert wake_source_from_dict(wake_source_to_dict(src)) == src


def test_roundtrip_manual() -> None:
    src = ManualWake()
    assert wake_source_from_dict(wake_source_to_dict(src)) == src


def test_from_dict_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        wake_source_from_dict({"kind": "doom"})


def test_from_dict_rejects_missing_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        wake_source_from_dict({})


def test_label_returns_short_string_for_prompt_insertion() -> None:
    """The runner uses ``label`` to splice the wake source into the stimulus.

    Keep it short — the spec caps the wake prompt at ~800 tokens and the
    label is the only dynamic piece.
    """
    assert CronWake(cron_kind="doc-garden").label == "cron:doc-garden"
    assert IdleTimerWake(idle_minutes=47).label == "idle_timer:47m"
    assert InboundMessageWake(channel="c", message_ref="r").label == "inbound_message:c"
    assert ManualWake().label == "manual"
