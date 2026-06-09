"""Auth-source → env-var mapping used by :mod:`dream.config.from_file`.

Splitting this out keeps the env-resolution table editable without touching
the :class:`~dream.config.from_file.Settings` model and lets adapters import
just the lookup without pulling pydantic.

Per Spec 02 decision 1 (env-overrides resolved at startup): the
``DREAM_*`` form takes precedence over the native vendor form, so an
operator can run multiple sessions against different keys without
clobbering the global ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import os

# Active-profile selector. Honoured by Settings.resolve_profile() when no
# explicit profile name is passed.
PROFILE_ENV = "DREAM_PROFILE"

# auth_source → ordered env-var candidates. First match wins.
_AUTH_ENV_CANDIDATES: dict[str, tuple[str, ...]] = {
    "anthropic_api_key": ("DREAM_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    "openai_api_key": ("DREAM_OPENAI_API_KEY", "OPENAI_API_KEY"),
    "azure_openai_api_key": (
        "DREAM_AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        # Azure deployments commonly reuse the OPENAI_API_KEY env var when
        # speaking OpenAI-compatible mode. Last-resort fallback.
        "OPENAI_API_KEY",
    ),
    "copilot_oauth": ("DREAM_COPILOT_TOKEN", "COPILOT_TOKEN"),
    "moonshot_api_key": ("DREAM_MOONSHOT_API_KEY", "MOONSHOT_API_KEY"),
    "gemini_api_key": ("DREAM_GEMINI_API_KEY", "GEMINI_API_KEY"),
    "deepseek_api_key": ("DREAM_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    "groq_api_key": ("DREAM_GROQ_API_KEY", "GROQ_API_KEY"),
    "mistral_api_key": ("DREAM_MISTRAL_API_KEY", "MISTRAL_API_KEY"),
    "openrouter_api_key": ("DREAM_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
}


def auth_source_env_var_candidates(auth_source: str) -> tuple[str, ...]:
    """Return the env-var search order for ``auth_source``, empty tuple if unknown."""
    return _AUTH_ENV_CANDIDATES.get(auth_source, ())


def resolve_auth_env_value(auth_source: str) -> tuple[str, str] | None:
    """Return the first ``(env_var, value)`` pair set in the environment, else ``None``."""
    for env_var in auth_source_env_var_candidates(auth_source):
        value = os.environ.get(env_var, "")
        if value:
            return env_var, value
    return None


# provider → auth_source for providers whose natural source isn't the
# generic ``{provider}_api_key`` (copilot is OAuth-backed, not key-backed).
_PROVIDER_AUTH_SOURCE: dict[str, str] = {
    "copilot": "copilot_oauth",
    "azure_openai": "azure_openai_api_key",
    "anthropic": "anthropic_api_key",
    "openai": "openai_api_key",
}


def default_auth_source_for_provider(provider: str, api_format: str | None = None) -> str:
    """Infer the natural auth source for a provider, with ``api_format`` as a tiebreaker.

    Used by :func:`Settings.resolve_auth` when a profile leaves ``auth_source`` blank
    so we don't force the operator to spell out the obvious wiring.
    """
    explicit = _PROVIDER_AUTH_SOURCE.get(provider)
    if explicit is not None:
        return explicit
    if provider:
        # A named OpenAI-compatible provider (groq, openrouter, deepseek, …) gets
        # its OWN key env, not openai's — api_format is not provider identity.
        return f"{provider}_api_key"
    # Provider unknown — only now use api_format as a last-resort hint.
    if api_format == "openai":
        return "openai_api_key"
    return "api_key"


__all__ = [
    "PROFILE_ENV",
    "auth_source_env_var_candidates",
    "default_auth_source_for_provider",
    "resolve_auth_env_value",
]
