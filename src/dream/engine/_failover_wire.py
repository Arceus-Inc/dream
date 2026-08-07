"""Build :class:`SubstrateSlot` graphs for :class:`FailoverStreamer` (factory seam).

Keeps credential-pool assembly out of :func:`dream._factory.build_harness` so
the factory stays a wiring shell rather than a god module.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from dream.api.credentials import Credential, CredentialPool, load_credential_pools
from dream.engine._adapter_openai import OpenAIChatStreamer, httpx_chat_completion_stream
from dream.engine._failover_streamer import FailoverStreamer
from dream.engine._loop import TurnStreamer
from dream.engine._substrate_slot import SubstrateSlot


@dataclass(frozen=True)
class StreamerParts:
    """Per-session knobs shared by every credential streamer on a substrate."""

    model: str
    base_url: str
    system_prompt: str
    extra_params: Mapping[str, object] | None


def single_key_pool(*, substrate: str, label: str, api_key: str) -> CredentialPool:
    """One-credential pool for the common env-key harness path."""
    if not api_key:
        raise ValueError("api_key must be non-empty")
    return CredentialPool(
        substrate,
        (Credential(label=label, key=api_key, substrate=substrate),),
    )


def openai_streamer_for_key(api_key: str, parts: StreamerParts) -> TurnStreamer:
    """Construct an OpenAI-compatible streamer pinned to one credential key."""
    return OpenAIChatStreamer(
        stream_chat_completion=httpx_chat_completion_stream(
            api_key=api_key,
            base_url=parts.base_url,
            extra_params=parts.extra_params,
        ),
        model=parts.model,
        system_prompt=parts.system_prompt,
    )


def slot_from_pool(
    *,
    pool: CredentialPool,
    parts: StreamerParts,
    make_streamer: Callable[[str, StreamerParts], TurnStreamer] = openai_streamer_for_key,
) -> SubstrateSlot:
    """Materialize one streamer per credential label in ``pool``."""
    streamers: dict[str, TurnStreamer] = {}
    for cred in pool.all_credentials():
        streamers[cred.label] = make_streamer(cred.key, parts)
    return SubstrateSlot(name=pool.substrate, pool=pool, streamers=streamers)


def slots_for_session(
    *,
    api_key: str,
    parts: StreamerParts,
    credentials_path: Path | None = None,
    active_substrate: str | None = None,
) -> tuple[SubstrateSlot, ...]:
    """Resolve session slots: optional ``credentials.toml``, else single env key.

    When ``credentials.toml`` is present, ``active_substrate`` is optional. If
    omitted, every non-empty pool in file order is used (so a file with only
    ``[[openai]]`` / ``[[azure]]`` is valid). If set, that pool is preferred
    first and ``load_credential_pools`` refuses an empty/missing active pool.
    """
    if credentials_path is not None and credentials_path.is_file():
        pools = load_credential_pools(credentials_path, active=active_substrate)
        ordered: list[CredentialPool] = []
        if (
            active_substrate is not None
            and active_substrate in pools
            and not pools[active_substrate].is_empty()
        ):
            ordered.append(pools[active_substrate])
        for name, pool in pools.items():
            if active_substrate is not None and name == active_substrate:
                continue
            if pool.is_empty():
                continue
            ordered.append(pool)
        if not ordered:
            raise ValueError(f"no usable credential pools in {credentials_path}")
        return tuple(slot_from_pool(pool=pool, parts=parts) for pool in ordered)

    substrate = active_substrate if active_substrate is not None else "primary"
    pool = single_key_pool(substrate=substrate, label="env", api_key=api_key)
    return (slot_from_pool(pool=pool, parts=parts),)


def build_failover_streamer(
    slots: Sequence[SubstrateSlot],
    *,
    retries_per_credential: int = 2,
) -> FailoverStreamer:
    return FailoverStreamer(slots, retries_per_credential=retries_per_credential)


__all__ = [
    "StreamerParts",
    "build_failover_streamer",
    "openai_streamer_for_key",
    "single_key_pool",
    "slot_from_pool",
    "slots_for_session",
]
