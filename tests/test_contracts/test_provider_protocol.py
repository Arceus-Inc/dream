"""Spec 02 contract surface that exists today: ``dream.contracts.provider``.

The substrate / credential-pool / failover machinery from Spec 02 isn't built
yet (see ``tests/test_api/test_spec02_contracts.py`` for those pins). What we
*do* have is the higher-level :class:`Provider` Protocol that the engine
streams through. This file locks in the shape of that Protocol and three
load-bearing rules around it:

- the dataclasses (``ProviderCapabilities``, ``ProviderUsage``, ``ProviderEvent``)
  carry the documented default values and are frozen, so the rest of the SDK
  can pass them around safely (Spec 02 decision 5 spirit: the loop branches off
  capability flags, not provider names).
- the Protocol is ``runtime_checkable`` and rejects classes that don't expose
  ``stream_messages`` / ``capabilities``, so a misshapen plugin fails the
  ``isinstance(p, Provider)`` check at registration rather than at call time.
- importing ``dream.contracts.provider`` does **not** pull in ``httpx`` or any
  vendor SDK — the contracts subpackage is the only thing sibling repos
  (chorus, lattice, horizon) are allowed to depend on, and its docstring
  explicitly forbids I/O dependencies.
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from dream.contracts.provider import (
    Provider,
    ProviderCapabilities,
    ProviderEvent,
    ProviderUsage,
)
from dream.contracts.tool import Tool

# --- ProviderCapabilities ---------------------------------------------------


def test_provider_capabilities_defaults() -> None:
    caps = ProviderCapabilities()
    assert caps.tool_use is True
    assert caps.streaming is True
    assert caps.prompt_cache is False
    assert caps.thinking_blocks is False
    assert caps.parallel_tool_calls is False
    assert caps.max_context_tokens is None


def test_provider_capabilities_frozen() -> None:
    caps = ProviderCapabilities()
    with pytest.raises(dataclasses.FrozenInstanceError):
        caps.tool_use = False  # type: ignore[misc]


# --- ProviderUsage ----------------------------------------------------------


def test_provider_usage_defaults_are_zero() -> None:
    usage = ProviderUsage()
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cache_read_tokens == 0
    assert usage.cache_write_tokens == 0
    assert usage.cost_usd == 0.0
    assert usage.metadata == {}


def test_provider_usage_metadata_default_is_independent() -> None:
    """Mutating one usage's metadata must not leak into another's default.

    This is the classic mutable-default-argument trap. ``field(default_factory=dict)``
    is the documented fix; verify it's actually wired that way.
    """
    a = ProviderUsage()
    b = ProviderUsage()
    a.metadata["k"] = "v"
    assert b.metadata == {}


def test_provider_usage_frozen() -> None:
    usage = ProviderUsage()
    with pytest.raises(dataclasses.FrozenInstanceError):
        usage.input_tokens = 1  # type: ignore[misc]


# --- ProviderEvent ----------------------------------------------------------


def test_provider_event_carries_type_and_opaque_data() -> None:
    """``type`` is the discriminator; ``data`` stays an opaque payload dict so
    this Protocol isn't coupled to any single vendor's event taxonomy."""
    ev = ProviderEvent(type="text_delta", data={"text": "hi"})
    assert ev.type == "text_delta"
    assert ev.data == {"text": "hi"}


def test_provider_event_data_default_is_independent() -> None:
    a = ProviderEvent(type="start")
    b = ProviderEvent(type="start")
    a.data["x"] = 1
    assert b.data == {}


# --- Provider Protocol membership ------------------------------------------


class _ConformingProvider:
    name = "fake"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    async def stream_messages(
        self,
        history: Sequence[dict[str, Any]],
        *,
        system: str,
        tools: Sequence[Tool],
        model: str,
        max_output_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        async def _gen() -> AsyncIterator[ProviderEvent]:
            yield ProviderEvent(type="end")

        return _gen()


class _MissingStreamMessages:
    name = "broken"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()


class _MissingCapabilities:
    name = "broken"

    async def stream_messages(self, *args: Any, **kwargs: Any) -> AsyncIterator[ProviderEvent]:  # type: ignore[override]
        async def _gen() -> AsyncIterator[ProviderEvent]:
            yield ProviderEvent(type="end")

        return _gen()


def test_provider_protocol_is_runtime_checkable() -> None:
    """A conforming class passes ``isinstance``. This is the whole point of
    ``@runtime_checkable`` — plugins can be validated at registration.
    """
    assert isinstance(_ConformingProvider(), Provider)


def test_provider_protocol_rejects_missing_stream_messages() -> None:
    assert not isinstance(_MissingStreamMessages(), Provider)


def test_provider_protocol_rejects_missing_capabilities() -> None:
    assert not isinstance(_MissingCapabilities(), Provider)


# --- contracts subpackage stays I/O-free -----------------------------------


_VENDOR_MODULES = ("httpx", "anthropic", "openai", "litellm", "requests")


def test_contracts_provider_module_does_not_import_vendor_sdks() -> None:
    """The contracts subpackage docstring says sibling repos depend on it and
    it must stay free of provider/I/O deps. A regression here would force
    chorus / lattice / horizon to install ``httpx`` etc.
    """
    # Drop any cached vendor imports so re-importing the contract module is a
    # fair test of *its* import graph, not the whole test runner's.
    for name in list(sys.modules):
        if any(name == v or name.startswith(f"{v}.") for v in _VENDOR_MODULES):
            sys.modules.pop(name, None)

    importlib.import_module("dream.contracts.provider")

    leaked = [v for v in _VENDOR_MODULES if v in sys.modules]
    assert not leaked, f"dream.contracts.provider pulled in vendor SDKs: {leaked}"
