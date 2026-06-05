"""LLM provider registry — single source of truth for detection metadata.

Adding a new provider is a *table row*, not a new code path: append a
``ProviderSpec`` to :data:`PROVIDERS` and detection, display, and config-shape
inference all derive from it (Spec 02 decision 4, criterion 3).

Order matters — earlier entries win on detection ties. The table is ordered:
gateways and OAuth first (most specific signals), then standard cloud
providers by model-name keyword, then local deployments last.

This module is intentionally I/O-free and SDK-free so it can be imported from
hot paths (CLI startup, request dispatch) without dragging in ``httpx``,
``anthropic``, or ``openai``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    """One LLM provider's detection metadata.

    ``backend_type`` classifies the wire protocol the runner must speak:

    - ``anthropic``      Anthropic Messages API.
    - ``openai_compat``  OpenAI Chat Completions REST (vanilla OpenAI, Azure
      OpenAI's ``/openai/v1`` path, LiteLLM, vLLM, Ollama, gateways, …).
    - ``copilot``        GitHub Copilot's OAuth-fronted endpoint.
    """

    name: str
    keywords: tuple[str, ...] = ()
    env_key: str = ""
    display_name: str = ""
    backend_type: str = "openai_compat"
    default_base_url: str = ""
    detect_by_key_prefix: str = ""
    detect_by_base_keyword: str = ""
    is_gateway: bool = False
    is_local: bool = False
    is_oauth: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


# Ordered list (not tuple) — Spec 02 test pin asserts ``isinstance(PROVIDERS, list)``
# precisely because operators need to reorder via config without recompilation.
PROVIDERS: list[ProviderSpec] = [
    # --- OAuth-fronted providers (detected by api_format / explicit selection) ---
    ProviderSpec(
        name="github_copilot",
        keywords=("copilot",),
        display_name="GitHub Copilot",
        backend_type="copilot",
        is_oauth=True,
    ),
    # --- Gateways: matched by api-key prefix or base-url substring ---
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        is_gateway=True,
    ),
    # --- Cloud platform providers: matched by base_url substring ---
    # Azure OpenAI sits ahead of vanilla OpenAI because its base-url pattern
    # is more specific. The ``/openai/v1`` compatibility path on
    # ``*.cognitiveservices.azure.com`` lets it ride the openai_compat backend
    # — Azure-specific quirks (api-version param, ``max_completion_tokens``
    # rename for reasoning models) belong in the adapter, not here.
    ProviderSpec(
        name="azure_openai",
        keywords=("azure",),
        env_key="AZURE_OPENAI_API_KEY",
        display_name="Azure OpenAI",
        detect_by_base_keyword="cognitiveservices.azure.com",
    ),
    # --- Standard cloud providers: matched by model-name keyword ---
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend_type="anthropic",
        detect_by_key_prefix="sk-ant-",
        detect_by_base_keyword="anthropic.com",
    ),
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt", "o1", "o3", "o4"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        detect_by_base_keyword="openai.com",
    ),
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        default_base_url="https://api.deepseek.com/v1",
        detect_by_base_keyword="deepseek",
    ),
    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        detect_by_base_keyword="googleapis",
    ),
    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        display_name="Moonshot",
        default_base_url="https://api.moonshot.ai/v1",
        detect_by_base_keyword="moonshot",
    ),
    ProviderSpec(
        name="mistral",
        keywords=("mistral", "mixtral", "codestral"),
        env_key="MISTRAL_API_KEY",
        display_name="Mistral",
        default_base_url="https://api.mistral.ai/v1",
        detect_by_base_keyword="mistral",
    ),
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        default_base_url="https://api.groq.com/openai/v1",
        detect_by_key_prefix="gsk_",
        detect_by_base_keyword="groq",
    ),
    # --- Local deployments: matched by keyword or base_url ---
    ProviderSpec(
        name="ollama",
        keywords=("ollama",),
        display_name="Ollama",
        default_base_url="http://localhost:11434/v1",
        detect_by_base_keyword="localhost:11434",
        is_local=True,
    ),
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        display_name="vLLM/Local",
        is_local=True,
    ),
]


def find_by_name(name: str) -> ProviderSpec | None:
    """Return the first :class:`ProviderSpec` whose ``name`` matches exactly."""
    for spec in PROVIDERS:
        if spec.name == name:
            return spec
    return None


def _match_by_model(model: str) -> ProviderSpec | None:
    """Match by model-name keyword (case-insensitive).

    Tries an explicit ``provider/model`` prefix first (e.g. ``"deepseek/v4"``
    → deepseek spec), then falls back to a substring scan over keywords.
    Local-only and OAuth-only specs are excluded so they don't shadow real
    cloud providers when a user names a Claude model.
    """
    model_lower = model.lower()
    model_normalized = model_lower.replace("-", "_")
    model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
    normalized_prefix = model_prefix.replace("-", "_")

    cloud = [s for s in PROVIDERS if not s.is_local and not s.is_oauth]
    local_or_oauth = [s for s in PROVIDERS if s.is_local or s.is_oauth]

    # Cloud specs match first, so a bare model keyword (e.g. a Claude model) is
    # never shadowed by a local provider. Local/OAuth specs are a fallback group,
    # so an explicit local model or prefix (e.g. "ollama/llama3") still resolves.
    for group in (cloud, local_or_oauth):
        for spec in group:
            if model_prefix and normalized_prefix == spec.name:
                return spec
        for spec in group:
            for kw in spec.keywords:
                if kw in model_lower or kw.replace("-", "_") in model_normalized:
                    return spec
    return None


def detect_provider(
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> ProviderSpec | None:
    """Detect the best-matching :class:`ProviderSpec` from any subset of signals.

    Detection priority (Spec 02 decision 4):

    1. ``api_key`` prefix (e.g. ``sk-or-`` → OpenRouter, ``sk-ant-`` → Anthropic).
    2. ``base_url`` substring (e.g. ``cognitiveservices.azure.com`` → Azure OpenAI).
    3. ``model`` name keyword.

    Returns ``None`` when nothing matches; the caller decides whether that's a
    configuration error or a soft fallback to ``api_format``.
    """
    if api_key:
        for spec in PROVIDERS:
            if spec.detect_by_key_prefix and api_key.startswith(spec.detect_by_key_prefix):
                return spec

    if base_url:
        base_lower = base_url.lower()
        for spec in PROVIDERS:
            if spec.detect_by_base_keyword and spec.detect_by_base_keyword in base_lower:
                return spec

    if model:
        return _match_by_model(model)

    return None


__all__ = ["PROVIDERS", "ProviderSpec", "detect_provider", "find_by_name"]
