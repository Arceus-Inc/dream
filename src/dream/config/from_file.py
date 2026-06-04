"""Settings, ProviderProfile, ResolvedAuth — Spec 02 config layer.

Resolves at startup; never hot-reloads (Spec 02 decision 1). The three load-
bearing exports are:

* :class:`Settings` — the JSON shape persisted under ``~/.dream/settings.json``
  (and the in-memory model the CLI mutates).
* :class:`ProviderProfile` — one named provider workflow inside a Settings.
  Carries the context-window handshake fields (criterion 4) that Spec 04
  consumes for prompt budgeting.
* :class:`ResolvedAuth` — the uniform shape every adapter receives so the
  client constructor never branches on provider (decision 3).

Auth resolution here is intentionally env-only and synchronous; the
credential-pool / TOML / file-permission layer is Spec 02 Stage 3.

This module is I/O-light: it reads env vars and (optionally) one JSON file
on demand. It does not import vendor SDKs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from dream.config.from_env import (
    PROFILE_ENV,
    default_auth_source_for_provider,
    resolve_auth_env_value,
)

# ---------------------------------------------------------------------------
# ResolvedAuth — the uniform shape (decision 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedAuth:
    """Normalised auth material handed to the adapter.

    ``auth_kind`` ∈ {``api_key``, ``oauth_device``, ``external_oauth``}
    captures every family OpenHarness's provider detection enumerates and
    every family the Spec-02 substrate adapters need to dispatch on. New
    kinds are a spec amendment, not a quiet additive change.

    ``state`` defaults to ``"configured"``. The other documented values
    (``"expired"``, ``"refreshing"``, ``"revoked"``) come into play once
    OAuth refresh lands in Stage 3.
    """

    provider: str
    auth_kind: str
    value: str
    source: str
    state: str = "configured"


# ---------------------------------------------------------------------------
# ProviderProfile — a named, self-contained provider workflow (decision 2)
# ---------------------------------------------------------------------------


class ProviderProfile(BaseModel):
    """One named provider workflow.

    The minimum field set is locked by the Spec 02 test pins; optional fields
    are additive. ``context_window_tokens`` and ``auto_compact_threshold_tokens``
    are the handshake to Spec 04's context budgeter (criterion 4): without them
    the budgeter has no input and falls back to a conservative built-in.
    """

    model_config = ConfigDict(frozen=False, extra="ignore")

    label: str
    provider: str
    api_format: str
    auth_source: str
    default_model: str
    base_url: str | None = None
    last_model: str | None = None
    credential_slot: str | None = None
    allowed_models: list[str] = Field(default_factory=list)
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None

    @property
    def resolved_model(self) -> str:
        """Return the live model id (``last_model`` overrides ``default_model``)."""
        configured = (self.last_model or "").strip()
        return configured or self.default_model


def default_provider_profiles() -> dict[str, ProviderProfile]:
    """Return the built-in profile catalogue.

    Keep the catalogue small and concrete — exotic providers are added in
    ``~/.dream/settings.json`` by operators, not vendored here.
    """
    return {
        "claude-api": ProviderProfile(
            label="Anthropic API",
            provider="anthropic",
            api_format="anthropic",
            auth_source="anthropic_api_key",
            default_model="claude-sonnet-4-6",
        ),
        "openai-compatible": ProviderProfile(
            label="OpenAI-Compatible API",
            provider="openai",
            api_format="openai",
            auth_source="openai_api_key",
            default_model="gpt-5-mini",
        ),
        "azure-openai": ProviderProfile(
            label="Azure OpenAI",
            provider="azure_openai",
            api_format="openai",
            auth_source="azure_openai_api_key",
            default_model="gpt-5-mini",
            # base_url is operator-supplied — Azure resources are subscription-scoped.
            base_url=None,
        ),
        "copilot": ProviderProfile(
            label="GitHub Copilot",
            provider="copilot",
            api_format="copilot",
            auth_source="copilot_oauth",
            default_model="gpt-5-mini",
        ),
    }


# ---------------------------------------------------------------------------
# Settings — startup-resolved config (decision 1)
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    """The full Spec-02 config surface, modelled after ``~/.dream/settings.json``.

    Decision 1: changes during a session do **not** take effect until the next
    start. That discipline lives at the call site (the runner reads once); this
    model just stores values immutably enough for the lifetime of a process.
    """

    model_config = ConfigDict(frozen=False, extra="ignore")

    active_profile: str = "claude-api"
    profiles: dict[str, ProviderProfile] = Field(default_factory=default_provider_profiles)

    # Optional flat overrides — populated from env or CLI before resolve_profile().
    # When non-None they win over the matching profile field.
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None

    def merged_profiles(self) -> dict[str, ProviderProfile]:
        """Return user profiles overlaid on the built-in catalogue.

        Built-in entries keep their original ``label`` and ``base_url`` when the
        user profile doesn't override them, so old persisted settings don't
        carry stale wording forward.
        """
        merged = default_provider_profiles()
        for name, profile in self.profiles.items():
            if not isinstance(profile, ProviderProfile):
                profile = ProviderProfile.model_validate(profile)
            builtin = merged.get(name)
            if builtin is not None and profile.base_url is None and builtin.base_url is not None:
                profile = profile.model_copy(update={"base_url": builtin.base_url})
            merged[name] = profile
        return merged

    def resolve_profile(self, name: str | None = None) -> tuple[str, ProviderProfile]:
        """Pick the active profile by name, env var, or ``active_profile`` field.

        Precedence: explicit ``name`` arg > ``$DREAM_PROFILE`` env > ``active_profile``
        field > ``"claude-api"`` fallback. Falls back to the first profile in the
        merged map if even that name is missing — better than crashing during
        diagnostic commands.
        """
        import os

        profiles = self.merged_profiles()
        chosen = (
            (name or "").strip()
            or os.environ.get(PROFILE_ENV, "").strip()
            or (self.active_profile or "").strip()
            or "claude-api"
        )
        if chosen not in profiles:
            chosen = next(iter(profiles))
        return chosen, profiles[chosen].model_copy(deep=True)

    def resolve_auth(self, profile: ProviderProfile | None = None) -> ResolvedAuth:
        """Resolve credentials for the active (or supplied) profile.

        Stage-1 scope: env-var only. The TOML credential pool and OAuth refresh
        flows land in Stage 3; this method's signature is stable across stages
        so adapters don't churn when the pool arrives.
        """
        if profile is None:
            _, profile = self.resolve_profile()

        auth_source = (profile.auth_source or "").strip() or default_auth_source_for_provider(
            profile.provider, profile.api_format
        )

        # 1. flat override on Settings (set by CLI / programmatic callers).
        if self.api_key:
            return ResolvedAuth(
                provider=profile.provider,
                auth_kind="api_key",
                value=self.api_key,
                source="settings.api_key",
            )

        # 2. env var.
        env_resolved = resolve_auth_env_value(auth_source)
        if env_resolved is not None:
            env_var, env_value = env_resolved
            return ResolvedAuth(
                provider=profile.provider,
                auth_kind="api_key",
                value=env_value,
                source=f"env:{env_var}",
            )

        # 3. nothing found — explicit error rather than silently returning empty,
        # so the adapter doesn't fire a request that will 401 anyway.
        raise ValueError(
            f"No credentials found for auth_source={auth_source!r}. "
            "Set the matching DREAM_*/native env var or configure api_key on Settings."
        )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_settings(path: str | Path) -> Settings:
    """Load and validate ``settings.json`` from ``path``.

    Missing file → defaults (so first-run UX is "it works"). Malformed JSON
    is surfaced as ``ValueError`` rather than silently swallowed — the operator
    needs to know their config file is broken before a long task fails halfway.
    """
    p = Path(path)
    if not p.exists():
        return Settings()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed settings.json at {p}: {exc}") from exc
    return Settings.model_validate(raw)


__all__ = [
    "ProviderProfile",
    "ResolvedAuth",
    "Settings",
    "default_provider_profiles",
    "load_settings",
]
