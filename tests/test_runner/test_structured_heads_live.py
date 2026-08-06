"""Live e2e: structured-output P1/P2 against Azure OpenAI.

Covers:
1. ``complete_structured`` (wire helper)
2. Planner head with native ``response_format`` JSON contract
3. Subagent first-attempt ``output_schema`` constraint + post-hoc validate

Skips cleanly when Azure credentials are unset.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from dream import build_harness
from dream.api.response_format import JsonSchema
from dream.api.structured import ChatMessage, ChatRole, StructuredContentType, complete_structured
from dream.planner import PlannerOutput
from dream.runner import make_planner_head
from dream.subagents._declaration import Subagent
from dream.subagents._inline_executor import run_subagent_session


def _load_azure_env() -> tuple[str, str, str] | None:
    chorus_env = Path(__file__).resolve().parents[3] / "chorus" / ".env"
    if chorus_env.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(chorus_env, override=True)
        except ImportError:
            pass
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    base_url = os.environ.get("AZURE_OPENAI_BASE_URL")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    if not (api_key and base_url and deployment):
        return None
    return api_key, base_url, deployment


pytestmark = pytest.mark.skipif(
    _load_azure_env() is None,
    reason="AZURE_OPENAI_API_KEY / BASE_URL / DEPLOYMENT unset",
)


_PING_SCHEMA = JsonSchema.of(
    {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["answer", "confidence"],
        "additionalProperties": False,
    }
)

_SUBAGENT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_live_complete_structured_json_schema() -> None:
    creds = _load_azure_env()
    assert creds is not None
    api_key, base_url, deployment = creds

    result = complete_structured(
        api_key=api_key,
        base_url=base_url,
        model=deployment,
        messages=[
            ChatMessage(
                role=ChatRole.USER,
                content=(
                    'Return JSON only: {"answer": "pong", "confidence": 0.9}. No prose, no fences.'
                ),
            )
        ],
        schema=_PING_SCHEMA,
        schema_name="ping",
        strict=True,
        timeout_seconds=90.0,
    )

    assert result.content_type is StructuredContentType.JSON
    assert isinstance(result.parsed, Mapping)
    assert result.parsed.get("answer") == "pong"
    assert isinstance(result.parsed.get("confidence"), (int, float))


@pytest.mark.asyncio
async def test_live_planner_head_returns_typed_ledger(tmp_path: Path) -> None:
    creds = _load_azure_env()
    assert creds is not None
    api_key, base_url, deployment = creds

    harness = build_harness(
        model=deployment,
        api_key=api_key,
        base_url=base_url,
        working_dir=tmp_path,
        max_turns=6,
        skills=False,
        memory=False,
        mcp=False,
        plugins=False,
    )
    async with harness:
        head = make_planner_head(harness)
        out = await head(
            "e2e-structured-plan",
            "Create exactly one file named hello.txt containing the word hello. "
            "Use a single ledger step. Do not over-split.",
        )

    assert isinstance(out, PlannerOutput)
    assert out.spec_markdown.strip()
    assert len(out.ledger.steps) >= 1
    assert out.ledger.steps[0].id.strip()
    assert out.ledger.steps[0].description.strip()
    assert out.ledger.task_id == "e2e-structured-plan"


@pytest.mark.asyncio
async def test_live_subagent_output_schema_first_attempt(tmp_path: Path) -> None:
    creds = _load_azure_env()
    assert creds is not None
    api_key, base_url, deployment = creds

    agent = Subagent(
        name="json_echo",
        description="Returns a tiny JSON object",
        tools=("read_file",),
        output_schema=_SUBAGENT_SCHEMA,
        strict=True,
        max_turns=3,
        system_prompt=(
            "You are json_echo. Reply with ONLY a JSON object matching "
            '{"answer": "<string>"}. No prose, no tools unless needed, no fences.'
        ),
    )
    harness = build_harness(
        model=deployment,
        api_key=api_key,
        base_url=base_url,
        working_dir=tmp_path,
        max_turns=3,
        skills=False,
        memory=False,
        mcp=False,
        plugins=False,
    )
    async with harness:
        result = await run_subagent_session(
            agent,
            prompt='Answer with {"answer": "pong"}.',
            harness=harness,
            parent_tools=frozenset({"read_file"}),
        )

    assert result.success, result.error
    assert result.warning is None
    payload = json.loads(result.output)
    assert payload["answer"]
