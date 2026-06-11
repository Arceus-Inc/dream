"""Spec 05 + REPL upgrade #2 -- ``python -m dream.repl session`` subcommand.

Acceptance pinned here:
- ``session`` subparser exists with ``--events``, ``--model``, ``--system``,
  ``--max-turns`` args and a stable default events path.
- ``build_default_harness`` reads ``DREAM_SMOKE_API_KEY``/``_MODEL``/
  ``_BASE_URL`` from the supplied mapping and constructs a ``Harness``
  whose ``_engine_factory`` produces a real ``QueryEngine`` wired to
  ``OpenAIChatStreamer`` + ``default_registry()`` + a process-wide
  ``AutoCompactState``. Missing required env returns a clear error.
- ``session_loop`` consumes one prompt at a time from ``input_func``,
  routes each public ``events.Event`` through ``handle_event`` (printing
  to stdout and writing JSONL), and exits cleanly on ``/quit`` or EOF.
- ``handle_event`` writes one JSONL line per typed event with a stable
  ``session.*`` discriminator and the event's payload fields.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from dream.contracts.provider import ProviderCapabilities
from dream.engine._cost import UsageSnapshot
from dream.engine._engine import QueryEngine
from dream.engine._messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from dream.events import (
    Compacted,
    Error,
    TextDelta,
    ToolUseResult,
    ToolUseStart,
    TurnComplete,
)
from dream.harness import Harness, HarnessConfig
from dream.repl.__main__ import _build_parser
from dream.repl._events import EventSink
from dream.repl._session import (
    _handle_slash,
    build_default_harness,
    handle_event,
    run_session_repl,
    session_loop,
)
from dream.repl._watch import _colour_for
from dream.services.compact._orchestrator import AutoCompactState
from dream.session import SessionOptions
from tests.test_engine._fakes import FakeDispatcher, FakeStreamer, FakeTurn
from tests.test_skills._helpers import write_skill

# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------


def test_session_subcommand_parses_default_events_path() -> None:
    parser = _build_parser()
    args = parser.parse_args(["session"])
    assert args.command == "session"
    assert args.events == Path(".dream") / "repl-events.jsonl"
    assert args.model is None
    assert args.system is None
    assert args.max_turns == 8


def test_session_subcommand_accepts_all_flags(tmp_path: Path) -> None:
    parser = _build_parser()
    events = tmp_path / "e.jsonl"
    args = parser.parse_args(
        [
            "session",
            "--events",
            str(events),
            "--model",
            "gpt-test",
            "--system",
            "be helpful",
            "--max-turns",
            "3",
        ]
    )
    assert args.events == events
    assert args.model == "gpt-test"
    assert args.system == "be helpful"
    assert args.max_turns == 3


@pytest.mark.parametrize("bad", ["0", "-1", "-5"])
def test_session_max_turns_rejects_non_positive(bad: str) -> None:
    """``--max-turns`` must reject 0/negative at parse time -- otherwise the
    engine does zero turns and returns nothing (#36)."""
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["session", "--max-turns", bad])


def test_session_max_turns_accepts_positive() -> None:
    parser = _build_parser()
    args = parser.parse_args(["session", "--max-turns", "1"])
    assert args.max_turns == 1


# ---------------------------------------------------------------------------
# Default harness construction from env
# ---------------------------------------------------------------------------


def test_build_default_harness_rejects_missing_env(tmp_path: Path) -> None:
    with pytest.raises(KeyError) as ei:
        build_default_harness(env={}, working_dir=tmp_path)
    # With NO env set, the error must name *both* required keys, not just
    # one -- an ``or`` here passes even if the message drops a key (#39).
    message = str(ei.value)
    assert "DREAM_SMOKE_API_KEY" in message
    assert "DREAM_SMOKE_MODEL" in message


def test_build_default_harness_returns_harness_with_engine_factory(
    tmp_path: Path,
) -> None:
    env = {
        "DREAM_SMOKE_API_KEY": "sk-test",
        "DREAM_SMOKE_MODEL": "gpt-test",
        "DREAM_SMOKE_BASE_URL": "http://127.0.0.1:9/v1",
    }
    harness = build_default_harness(env=env, working_dir=tmp_path)
    assert isinstance(harness, Harness)
    assert harness.config._engine_factory is not None
    engine = harness.config._engine_factory("sid", SessionOptions(model="gpt-test"))
    assert isinstance(engine, QueryEngine)
    assert engine.session_id == "sid"
    # The compactor is wired so Slice E auto-compaction is active.
    assert engine.compactor is not None


def test_build_default_harness_honours_dream_home_for_task_storage(
    tmp_path: Path,
) -> None:
    """``DREAM_HOME`` in env must redirect task storage, not ``~/.dream`` (#43).

    The harness builds its ``BackgroundTaskManager`` from ``DreamPaths``; if it
    hardcodes ``Path.home()`` instead of resolving the home root from env, task
    artifacts land under the wrong root in environments that set ``DREAM_HOME``.
    """
    home = tmp_path / "dream-home"
    env = {
        "DREAM_SMOKE_API_KEY": "sk-test",
        "DREAM_SMOKE_MODEL": "gpt-test",
        "DREAM_HOME": str(home),
    }
    harness = build_default_harness(env=env, working_dir=tmp_path / "repo")
    task_manager = harness.config.task_manager
    assert task_manager is not None
    # tasks_dir == <home>/data/tasks per DreamPaths; must be under DREAM_HOME.
    assert home in task_manager._tasks_dir.parents


def test_build_default_harness_surfaces_stale_promotion_warnings(
    tmp_path: Path,
) -> None:
    """Policy-assembly warnings (e.g. a stale tier promotion) must reach the
    operator via the warning sink, not be silently discarded (#47)."""
    overrides = tmp_path / ".harness" / "tool-tier-overrides.toml"
    overrides.parent.mkdir(parents=True, exist_ok=True)
    overrides.write_text(
        '[bash]\n'
        'tier_required = "repo-write"\n'
        'promoted_at = "2000-01-01T00:00:00+00:00"\n',  # > 365 days old → stale
        encoding="utf-8",
    )
    env = {"DREAM_SMOKE_API_KEY": "sk-test", "DREAM_SMOKE_MODEL": "gpt-test"}
    warnings: list[str] = []
    build_default_harness(
        env=env,
        working_dir=tmp_path,
        policy_warning_sink=warnings.append,
    )
    assert any("stale" in w for w in warnings)
    assert any("bash" in w for w in warnings)


# ---------------------------------------------------------------------------
# handle_event: typed event -> stdout + JSONL
# ---------------------------------------------------------------------------


def test_handle_event_text_delta_writes_text_and_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(TextDelta(text="hello"), sink=sink, output=out)
    assert out.getvalue() == "hello"
    lines = (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[-1])
    assert payload["type"] == "session.text_delta"
    assert payload["text"] == "hello"


def test_handle_event_tool_use_start_writes_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(
        ToolUseStart(tool_use_id="tu_1", name="bash", input={"cmd": "ls"}),
        sink=sink,
        output=out,
    )
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "session.tool_use_start"
    assert payload["tool_use_id"] == "tu_1"
    assert payload["name"] == "bash"
    assert payload["input"] == {"cmd": "ls"}


def test_handle_event_tool_use_result_writes_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(
        ToolUseResult(tool_use_id="tu_1", name="bash", content="ok", is_error=False),
        sink=sink,
        output=out,
    )
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "session.tool_use_result"
    assert payload["is_error"] is False


def test_handle_event_turn_complete_writes_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(
        TurnComplete(stop_reason="end_turn", usage={"in": 3, "out": 5}),
        sink=sink,
        output=out,
    )
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "session.turn_complete"
    assert payload["stop_reason"] == "end_turn"


def test_handle_event_compacted_writes_context_event(tmp_path: Path) -> None:
    """Compacted maps to ``context.compaction.completed`` so the watch
    panel's colour table (REPL upgrade #3) can pick it up alongside the
    Spec 04 context-log events.
    """
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(
        Compacted(removed_messages=3, summary_tokens=400),
        sink=sink,
        output=out,
    )
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "context.compaction.completed"
    assert payload["removed_messages"] == 3
    assert payload["summary_tokens"] == 400


def test_handle_event_error_writes_jsonl(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    handle_event(Error(code="engine", message="boom"), sink=sink, output=out)
    payload = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["type"] == "session.error"
    assert payload["code"] == "engine"
    assert payload["message"] == "boom"


# ---------------------------------------------------------------------------
# session_loop: drives a real Session backed by FakeStreamer
# ---------------------------------------------------------------------------


def _engine_factory(streamer: FakeStreamer) -> Any:
    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    return _factory


def _scripted_input(lines: list[str]):
    """Return an ``input_func`` that yields scripted lines then EOF."""
    it = iter(lines)

    def _read(prompt: str = "") -> str:
        try:
            return next(it)
        except StopIteration as exc:
            raise EOFError from exc

    return _read


async def test_session_loop_quits_on_quit_command(tmp_path: Path) -> None:
    streamer = FakeStreamer(turns=[])
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    await session_loop(
        session=session,
        sink=sink,
        input_func=_scripted_input(["/quit"]),
        output=out,
    )
    # The banner printed...
    assert "session " in out.getvalue()
    # ...but ``/quit`` must NOT have triggered a model turn: the streamer
    # saw zero calls and no ``session.turn_failed`` was recorded (#40).
    assert streamer.calls == []
    events_file = tmp_path / "e.jsonl"
    if events_file.exists():
        types = [
            json.loads(line)["type"]
            for line in events_file.read_text(encoding="utf-8").splitlines()
        ]
        assert "session.turn_failed" not in types


async def test_session_loop_streams_send_events(tmp_path: Path) -> None:
    """One scripted user line, one fake assistant turn -> the loop yields
    TextDelta + TurnComplete to stdout and JSONL, then EOF cleanly exits.
    """
    streamer = FakeStreamer(
        turns=[
            FakeTurn(
                text_chunks=["hi ", "there"],
                usage=UsageSnapshot(input_tokens=2, output_tokens=2),
            )
        ]
    )
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    await session_loop(
        session=session,
        sink=sink,
        input_func=_scripted_input(["hello"]),
        output=out,
    )
    text = out.getvalue()
    assert "hi " in text
    assert "there" in text
    lines = [
        json.loads(line) for line in (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    types = [r["type"] for r in lines]
    assert "session.text_delta" in types
    assert "session.turn_complete" in types


async def test_session_loop_info_command_prints_session_id(tmp_path: Path) -> None:
    streamer = FakeStreamer(turns=[])
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    await session_loop(
        session=session,
        sink=sink,
        input_func=_scripted_input(["/info", "/quit"]),
        output=out,
    )
    assert session.id in out.getvalue()


async def test_session_loop_unknown_slash_keeps_running(tmp_path: Path) -> None:
    streamer = FakeStreamer(turns=[])
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    await session_loop(
        session=session,
        sink=sink,
        input_func=_scripted_input(["/nope", "/quit"]),
        output=out,
    )
    assert "unknown" in out.getvalue().lower()


# ---------------------------------------------------------------------------
# run_session_repl: top-level entry — accepts injected harness
# ---------------------------------------------------------------------------


def test_run_session_repl_with_injected_harness_returns_zero(
    tmp_path: Path,
) -> None:
    streamer = FakeStreamer(turns=[])
    harness = Harness(HarnessConfig(_engine_factory=_engine_factory(streamer)))  # type: ignore[call-arg]
    out = io.StringIO()
    rc = run_session_repl(
        events_path=tmp_path / "e.jsonl",
        working_dir=tmp_path,  # clean worktree: skip the session-start threat scan
        harness=harness,
        input_func=_scripted_input(["/quit"]),
        output=out,
    )
    assert rc == 0
    lines = [
        json.loads(line) for line in (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    types = [r["type"] for r in lines]
    assert "session.repl.started" in types
    assert "session.repl.stopped" in types


def test_run_session_repl_without_env_or_harness_returns_nonzero(
    tmp_path: Path,
) -> None:
    out = io.StringIO()
    rc = run_session_repl(
        events_path=tmp_path / "e.jsonl",
        env={},
        input_func=_scripted_input([]),
        output=out,
    )
    assert rc != 0


def test_injected_harness_validates_against_its_working_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``working_dir`` is omitted, an injected harness's configured repo
    root — not the process cwd — must drive skill validation/registry (#36).

    Proof: a malformed skill under the harness's working_dir blocks the session
    (code 3). If validation read ``Path.cwd()`` instead, the session would not
    see this skill and would not block here.
    """
    monkeypatch.setenv("DREAM_HOME", str(tmp_path / "home"))  # isolate user skills
    # Chdir to a *clean* dir so any cwd-based validation finds nothing — the only
    # malformed skill lives under the harness's configured working_dir.
    clean_cwd = tmp_path / "clean-cwd"
    clean_cwd.mkdir()
    monkeypatch.chdir(clean_cwd)
    repo = tmp_path / "configured-repo"
    repo.mkdir()
    write_skill(repo / "docs" / "skills", "bad", raw="not valid frontmatter")
    streamer = FakeStreamer(turns=[])
    harness = Harness(
        HarnessConfig(working_dir=repo, _engine_factory=_engine_factory(streamer))  # type: ignore[call-arg]
    )
    out = io.StringIO()
    rc = run_session_repl(
        events_path=tmp_path / "e.jsonl",
        harness=harness,  # no working_dir passed -> must fall back to repo, not cwd
        input_func=_scripted_input(["/quit"]),
        output=out,
    )
    assert rc == 3
    assert "blocked" in out.getvalue().lower()


def test_blocking_mcp_finding_still_unsubscribes_listeners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocking MCP finding makes the run return early (code 3); the task
    lifecycle listeners registered beforehand must still be unsubscribed so
    they don't leak / double-render on a later run (#44)."""
    monkeypatch.setenv("DREAM_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)  # clean cwd so only the MCP gate blocks
    # Malformed allowlist → setup_mcp_session returns a blocking finding.
    allow = tmp_path / ".harness" / "mcp-allowlist.toml"
    allow.parent.mkdir(parents=True, exist_ok=True)
    allow.write_text("this = = not toml", encoding="utf-8")

    import dream.repl._session as session_mod

    captured: list[Any] = []
    real_build = session_mod.build_default_harness

    def _capturing_build(**kwargs: Any) -> Any:
        harness = real_build(**kwargs)
        captured.append(harness)
        return harness

    monkeypatch.setattr(session_mod, "build_default_harness", _capturing_build)

    out = io.StringIO()
    rc = run_session_repl(
        events_path=tmp_path / "e.jsonl",
        env={"DREAM_SMOKE_API_KEY": "sk-test", "DREAM_SMOKE_MODEL": "gpt-test"},
        working_dir=tmp_path,
        input_func=_scripted_input([]),
        output=out,
    )

    assert rc == 3
    assert captured, "harness should have been built via the self-built path"
    task_manager = captured[0].config.task_manager
    assert task_manager is not None
    # Both listener registries must be empty — the outer finally ran the unsubs
    # despite the early return at the MCP gate.
    assert task_manager._start_listeners == {}
    assert task_manager._listeners == {}


def test_run_session_repl_emits_stopped_even_on_exception(tmp_path: Path) -> None:
    """If the loop raises, the stop lifecycle event must still be written so
    the JSONL watch panel always sees a terminal event (#38).
    """

    def _boom_factory(session_id: str, options: SessionOptions) -> QueryEngine:
        raise RuntimeError("engine construction blew up")

    harness = Harness(HarnessConfig(_engine_factory=_boom_factory))  # type: ignore[call-arg]
    events_path = tmp_path / "e.jsonl"
    out = io.StringIO()

    with pytest.raises(RuntimeError, match="blew up"):
        run_session_repl(
            events_path=events_path,
            working_dir=tmp_path,  # clean worktree: skip the session-start threat scan
            harness=harness,
            input_func=_scripted_input(["hi"]),
            output=out,
        )

    types = [
        json.loads(line)["type"]
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert "session.repl.started" in types
    assert "session.repl.stopped" in types


# ---------------------------------------------------------------------------
# REPL upgrade #3 -- /util and /compact slash commands
# ---------------------------------------------------------------------------


def _engine_factory_with_compactor(
    streamer: FakeStreamer,
    *,
    compactor: AutoCompactState,
    capabilities: ProviderCapabilities,
):
    """Like ``_engine_factory`` but wires the Spec 04 compaction inputs.

    The two extra fields are what ``/util`` and ``/compact`` read off the
    Session's bound engine. ``compactor`` must be non-``None`` for
    ``/compact`` to do anything; the capabilities supply the denominator
    for ``utilisation``.
    """

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
            compactor=compactor,
            compaction_capabilities=capabilities,
        )

    return _factory


async def _session_with_compactor() -> tuple[Any, AutoCompactState, ProviderCapabilities]:
    compactor = AutoCompactState()
    capabilities = ProviderCapabilities(max_context_tokens=128_000)
    streamer = FakeStreamer(turns=[])
    harness = Harness(
        HarnessConfig(  # type: ignore[call-arg]
            _engine_factory=_engine_factory_with_compactor(
                streamer, compactor=compactor, capabilities=capabilities
            )
        )
    )
    session = await harness.start_session()
    return session, compactor, capabilities


async def test_handle_slash_util_prints_utilisation_and_cost(tmp_path: Path) -> None:
    """``/util`` reports utilisation% against the engine's capabilities and
    the Session's running cost. Emits a ``session.util`` mirror event so
    the watch panel can record it.
    """
    session, _compactor, _caps = await _session_with_compactor()
    session._transcript.append(ConversationMessage(role="user", content=[TextBlock(text="hi")]))
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    keep = _handle_slash("/util", session=session, sink=sink, output=out)
    assert keep is True
    text = out.getvalue().lower()
    assert "util" in text
    assert "%" in text
    assert "cost" in text or "in=" in text
    lines = (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[-1])
    assert payload["type"] == "session.util"


async def test_handle_slash_compact_runs_microcompact_and_emits_event(
    tmp_path: Path,
) -> None:
    """``/compact`` forces a manual compaction via ``auto_compact_if_needed``
    (``trigger="manual"``, ``force=True``) so older compactable tool
    results are replaced with the cleared-content sentinel. The mirror
    event lands as ``context.compaction.completed`` so REPL watch
    colours it consistently with Spec 04 events.
    """
    session, _compactor, _caps = await _session_with_compactor()
    # Seed the transcript with >5 compactable tool_use/result pairs so
    # microcompact has something to clear (DEFAULT_KEEP_RECENT == 5).
    big_blob = "X" * 4096
    for i in range(8):
        tu_id = f"tu_{i}"
        session._transcript.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=tu_id, name="bash", input={"cmd": "ls"})],
            )
        )
        session._transcript.append(
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id=tu_id, content=big_blob, is_error=False)],
            )
        )
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()

    pre_blob_count = sum(
        1
        for msg in session._transcript
        for block in msg.content
        if isinstance(block, ToolResultBlock) and block.content == big_blob
    )
    assert pre_blob_count == 8

    keep = _handle_slash("/compact", session=session, sink=sink, output=out)
    assert keep is True

    post_blob_count = sum(
        1
        for msg in session._transcript
        for block in msg.content
        if isinstance(block, ToolResultBlock) and block.content == big_blob
    )
    # At least some older tool results should now carry the cleared marker.
    assert post_blob_count < pre_blob_count

    lines = (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines]
    types = [p["type"] for p in payloads]
    assert "context.compaction.completed" in types
    assert any("compact" in line.lower() for line in out.getvalue().splitlines())


async def test_handle_slash_compact_reports_noop_when_nothing_to_compact(
    tmp_path: Path,
) -> None:
    """With a compactor wired but a tiny transcript, ``/compact`` reclaims
    nothing. Because it forces ``force=True``, ``result is None`` is never
    reached -- the no-op must instead be detected via pre/post deltas and
    reported as ``session.compact_skipped`` (#37).
    """
    session, _compactor, _caps = await _session_with_compactor()
    # A single short user message -- nothing compactable.
    session._transcript.append(ConversationMessage(role="user", content=[TextBlock(text="hi")]))
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()

    keep = _handle_slash("/compact", session=session, sink=sink, output=out)
    assert keep is True

    payloads = [
        json.loads(line)
        for line in (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    types = [p["type"] for p in payloads]
    # Must report a skip, NOT a completed compaction.
    assert "session.compact_skipped" in types
    assert "context.compaction.completed" not in types
    assert "nothing to compact" in out.getvalue().lower()


async def test_handle_slash_compact_no_compactor_warns(tmp_path: Path) -> None:
    """If the engine has no compactor wired, ``/compact`` is a no-op that
    prints a helpful message instead of crashing.
    """
    streamer = FakeStreamer(turns=[])

    def _factory(session_id: str, options: SessionOptions) -> QueryEngine:
        return QueryEngine(
            streamer=streamer,
            dispatcher=FakeDispatcher(),
            session_id=session_id,
            working_dir=Path("/tmp"),
            max_turns=options.max_turns or 4,
        )

    harness = Harness(HarnessConfig(_engine_factory=_factory))  # type: ignore[call-arg]
    session = await harness.start_session()
    sink = EventSink(tmp_path / "e.jsonl")
    out = io.StringIO()
    keep = _handle_slash("/compact", session=session, sink=sink, output=out)
    assert keep is True
    text = out.getvalue().lower()
    assert "compact" in text
    assert "not" in text or "disabled" in text or "no compactor" in text


# ---------------------------------------------------------------------------
# REPL upgrade #3 -- watch colour table
# ---------------------------------------------------------------------------


def test_watch_colour_for_context_compaction_completed_is_cyan() -> None:
    """``context.compaction.completed`` is the public-facing 'compaction
    succeeded' event the REPL watch should highlight in cyan.
    """
    code = _colour_for("context.compaction.completed")
    assert code != ""
    assert "36" in code  # ANSI cyan


def test_watch_colour_for_context_compaction_triggered_is_yellow() -> None:
    """``context.compaction.triggered`` signals pressure; render in yellow."""
    code = _colour_for("context.compaction.triggered")
    assert code != ""
    assert "33" in code  # ANSI yellow


def test_watch_colour_for_session_turn_complete_is_green() -> None:
    code = _colour_for("session.turn_complete")
    assert "32" in code  # ANSI green


def test_watch_colour_for_session_error_is_red() -> None:
    code = _colour_for("session.error")
    assert "31" in code  # ANSI red


def test_watch_colour_for_session_repl_started_is_cyan() -> None:
    code = _colour_for("session.repl.started")
    assert "36" in code  # ANSI cyan


# ---------------------------------------------------------------------------
# Spec 06.5 slice 2 -- wake-cycle event colours
# ---------------------------------------------------------------------------


def test_watch_colour_for_heartbeat_decision_run_is_green() -> None:
    code = _colour_for("heartbeat.decision.run")
    assert "32" in code  # ANSI green — agent chose to do work


def test_watch_colour_for_heartbeat_decision_skip_is_dim() -> None:
    """A skip is normal background noise: dim it so a watching human's
    eye skips it too."""
    code = _colour_for("heartbeat.decision.skip")
    assert code != ""
    # Either ANSI dim (2) or grey 90; both are acceptable signals of
    # de-emphasised output.
    assert "2" in code or "90" in code


def test_watch_colour_for_heartbeat_decision_forced_is_yellow() -> None:
    """Forced wake = anti-coma guard tripped. This is noteworthy."""
    code = _colour_for("heartbeat.decision.forced")
    assert "33" in code  # ANSI yellow


def test_watch_colour_for_heartbeat_missing_is_red() -> None:
    """``heartbeat_missing_decision`` means the model failed to produce a
    valid heartbeat — surface it loudly."""
    code = _colour_for("heartbeat.missing")
    assert "31" in code  # ANSI red


def test_watch_colour_for_wake_dropped_is_dim() -> None:
    """Dropped wake = overlap dedup. Not an error, just informational."""
    code = _colour_for("wake.dropped")
    assert code != ""
    assert "2" in code or "90" in code
