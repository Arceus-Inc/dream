"""Unit pins for REPL dispatcher + event sink — Spec 02 surface, dev tool layer."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from dream.api.credentials import Credential
from dream.api.failover import NoLiveSubstrate
from dream.api.substrate import CompletionResult, HealthReport
from dream.repl._chat import (
    Dispatcher,
    SubstrateSpec,
    Transcript,
    _classify_exception,
)
from dream.repl._events import EventSink
from dream.repl._fake import FakeFailingSubstrate

# --- _classify_exception -------------------------------------------------


class _AuthenticationError(Exception):
    pass


class _PermissionDeniedError(Exception):
    pass


class _BadRequestError(Exception):
    pass


class _APITimeoutError(Exception):
    pass


class _APIConnectionError(Exception):
    pass


class _RateLimitError(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_AuthenticationError("bad key"), "auth"),
        (_PermissionDeniedError("forbidden"), "auth"),
        (_BadRequestError("malformed"), "hard_refusal"),
        (_APITimeoutError("slow"), "transient_exhausted"),
        (_APIConnectionError("net"), "transient_exhausted"),
        (_RateLimitError("429"), "transient_exhausted"),
        (RuntimeError("unknown"), "transient_exhausted"),  # conservative default
    ],
)
def test_classify_exception_table(exc: Exception, expected: str) -> None:
    assert _classify_exception(exc) == expected


# --- EventSink -----------------------------------------------------------


def test_event_sink_writes_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events.jsonl")
    sink.emit("test.event", k=1, s="hi")
    sink.emit("test.other", x=True)
    lines = sink.path.read_text("utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert records[0]["type"] == "test.event"
    assert records[0]["k"] == 1
    assert records[0]["s"] == "hi"
    assert "ts" in records[0]
    assert "pid" in records[0]
    assert records[1]["type"] == "test.other"
    assert records[1]["x"] is True


def test_event_sink_failover_callback(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events.jsonl")
    sink.callback({"type": "substrate.failover", "from": "a", "to": "b", "reason": "x"})
    records = [json.loads(line) for line in sink.path.read_text("utf-8").splitlines()]
    assert records[0]["type"] == "substrate.failover"
    assert records[0]["from"] == "a"
    assert records[0]["to"] == "b"


# --- Dispatcher with fakes -----------------------------------------------


class _OkSubstrate:
    """Always-succeeding substrate stub for testing the happy path."""

    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def complete(self, prompt: str, params: dict[str, Any] | None = None) -> CompletionResult:
        return CompletionResult(
            text=f"<{self.name}> echo: {prompt[:20]}",
            input_tokens=10,
            output_tokens=5,
            finish_reason="stop",
        )

    def stream(self, prompt: str, params: dict[str, Any] | None = None) -> Iterator[str]:
        yield f"<{self.name}> "
        yield "ok"

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    def max_window(self) -> int:
        return 8_192

    def health(self) -> HealthReport:
        return HealthReport(state="ok", detail="", latency_ms=1.0)


def _ok_spec(name: str) -> SubstrateSpec:
    spec = SubstrateSpec(
        name=name,
        model="ok",
        base_url=None,
        max_window=8_192,
        timeout_seconds=5.0,
        credentials=[Credential(label="only", key="ok", substrate=name)],
        builder=lambda _cred: _OkSubstrate(name),
    )
    return spec


def _fake_spec(name: str) -> SubstrateSpec:
    return SubstrateSpec(
        name=name,
        model="fake",
        base_url=None,
        max_window=8_192,
        timeout_seconds=5.0,
        credentials=[Credential(label="fake-key", key="fake", substrate=name)],
        builder=lambda _cred: FakeFailingSubstrate(name=name),
    )


def test_dispatcher_happy_path(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events.jsonl")
    disp = Dispatcher([_ok_spec("primary")], sink)
    result = disp.complete("hello")
    assert result.substrate == "primary"
    assert result.label == "only"
    assert "echo" in result.text


def test_dispatcher_fails_over_to_next_substrate(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events.jsonl")
    disp = Dispatcher([_fake_spec("bad"), _ok_spec("good")], sink)
    result = disp.complete("hello")
    assert result.substrate == "good"
    # Bad substrate's credential is benched at rung=3 (auth outcome).
    bad_cred = disp.pools["bad"].get("fake-key")
    assert bad_cred.rung == 3
    assert bad_cred.is_benched()
    # And a failover event was emitted.
    records = [json.loads(line) for line in sink.path.read_text("utf-8").splitlines()]
    types = [r["type"] for r in records]
    assert "turn.attempt_failed" in types
    assert "substrate.failover" in types
    assert "turn.completed" in types


def test_dispatcher_chain_exhaustion_raises(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "events.jsonl")
    disp = Dispatcher([_fake_spec("bad1"), _fake_spec("bad2")], sink)
    with pytest.raises(NoLiveSubstrate):
        disp.complete("hello")


def test_dispatcher_streaming_happy_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sink = EventSink(tmp_path / "events.jsonl")
    disp = Dispatcher([_ok_spec("primary")], sink)
    result = disp.stream("hi")
    out = capsys.readouterr().out
    assert "ok" in out
    assert result.substrate == "primary"
    assert "ok" in result.text


def test_dispatcher_streaming_fails_over_when_first_substrate_errors_before_emit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sink = EventSink(tmp_path / "events.jsonl")
    # Fake raises before yielding any chunk → mid-turn rule doesn't bite,
    # failover proceeds to the next substrate.
    disp = Dispatcher([_fake_spec("bad"), _ok_spec("good")], sink)
    result = disp.stream("hi")
    assert result.substrate == "good"
    out = capsys.readouterr().out
    assert "ok" in out


# --- Transcript ----------------------------------------------------------


def test_transcript_render_single_user() -> None:
    t = Transcript()
    t.add_user("hello")
    rendered = t.render()
    assert "[User]" in rendered
    assert "hello" in rendered
    assert rendered.rstrip().endswith("[Assistant]")


def test_transcript_render_includes_system_when_set() -> None:
    t = Transcript(system="be brief")
    t.add_user("hi")
    rendered = t.render()
    assert "[System]" in rendered
    assert "be brief" in rendered


def test_transcript_reset_drops_messages_keeps_system() -> None:
    t = Transcript(system="keep me")
    t.add_user("a")
    t.add_assistant("b")
    t.reset()
    assert t.messages == []
    assert t.system == "keep me"
