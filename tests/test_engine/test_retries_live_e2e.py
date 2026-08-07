"""Optional live e2e: pooled FailoverStreamer path via build_harness against Azure.

Skipped unless Azure OpenAI env vars are present (loads sibling chorus ``.env``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dream.events import TextDelta

pytestmark = pytest.mark.asyncio


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


@pytest.mark.skipif(_load_azure_env() is None, reason="Azure OpenAI env unset")
async def test_live_harness_turn_via_pooled_failover(tmp_path: Path) -> None:
    import dream

    creds = _load_azure_env()
    assert creds is not None
    api_key, base_url, deployment = creds

    harness = dream.build_harness(
        model=deployment,
        api_key=api_key,
        base_url=base_url,
        working_dir=tmp_path,
        max_turns=2,
        skills=False,
        memory=False,
        mcp=False,
        plugins=False,
    )
    session = await harness.start_session()
    text_parts: list[str] = []
    async for event in session.send("Reply with exactly the word pong and nothing else."):
        if isinstance(event, TextDelta):
            text_parts.append(event.text)
    assert "pong" in "".join(text_parts).lower()
