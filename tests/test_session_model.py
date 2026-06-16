"""Tests for Session.model property (piece 1 of token-metering).

Session.model mirrors the bound engine's model field when an engine is
present, and returns "" when no engine is bound.
"""

from __future__ import annotations

from pathlib import Path

from dream.engine._engine import QueryEngine
from dream.session import Session, SessionOptions
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn


def _make_engine(model: str) -> QueryEngine:
    return QueryEngine(
        streamer=FakeStreamer(turns=[FakeTurn(text_chunks=["ok"])]),
        dispatcher=FakeDispatcher(),
        session_id="test-session",
        working_dir=Path("/tmp"),
        model=model,
    )


def test_session_model_returns_engine_model_when_engine_bound() -> None:
    engine = _make_engine("gpt-x")
    session = Session(id="s1", options=SessionOptions(), _engine=engine)
    assert session.model == "gpt-x"


def test_session_model_returns_empty_string_when_no_engine() -> None:
    session = Session(id="s2")
    assert session.model == ""


def test_session_model_returns_empty_string_for_engine_with_empty_model() -> None:
    engine = _make_engine("")
    session = Session(id="s3", options=SessionOptions(), _engine=engine)
    assert session.model == ""


def test_session_model_reflects_different_model_ids() -> None:
    for model_id in ("claude-3-5-sonnet", "gpt-4o", "gemini-pro"):
        engine = _make_engine(model_id)
        session = Session(id="sx", options=SessionOptions(), _engine=engine)
        assert session.model == model_id
