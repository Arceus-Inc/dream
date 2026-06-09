"""OpenAI-compatible substrate adapter.

Handles vanilla OpenAI, Azure OpenAI (via the ``/openai/v1`` compatibility
path on ``*.cognitiveservices.azure.com``), LiteLLM, vLLM, Ollama, OpenRouter,
DeepSeek, Gemini's OpenAI-compat surface — anything that speaks the OpenAI
Chat Completions REST shape.

**Azure OpenAI note.** Use the OpenAI-compatible base URL
``https://<resource>.cognitiveservices.azure.com/openai/v1`` and treat it as a
vanilla OpenAI deployment. The legacy deployment-routed URL returns 404 on
newer model deployments; the ``/openai/v1`` path is the supported escape
hatch and lets the same code path serve every OpenAI-compatible substrate.

**Reasoning-model quirk.** GPT-5 and the o1/o3/o4 reasoning families reject
``max_tokens`` and require ``max_completion_tokens`` instead. The translation
lives in :mod:`dream.api._wire` so this substrate and the Spec-03 engine
adapter share one table; the runner still does not branch on substrate name
(Spec 02 decision 6).
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from dream.api._timeout import DEFAULT_TIMEOUT_SECONDS, Deadline, SubstrateTimeout
from dream.api._wire import token_limit_param
from dream.api.substrate import CompletionResult, HealthReport, HealthState

if TYPE_CHECKING:
    from openai import OpenAI


def _approx_token_count(text: str) -> int:
    """Cheap GPT-style approximation: ~4 chars/token.

    The substrate's :meth:`count_tokens` is consulted for budget decisions,
    not billing reconciliation; the spec calls for a portable estimate, not a
    per-substrate tokenizer dependency. Adapters that need exact counts can
    subclass and override.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


class OpenAIChatSubstrate:
    """OpenAI Chat Completions substrate (vanilla OpenAI + Azure-compat + gateways).

    Constructor takes everything as explicit kwargs so the credential pool
    (Stage 3) can rebuild the substrate per credential without re-reading
    settings.
    """

    name: str

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        max_window_tokens: int = 128_000,
        default_params: dict[str, Any] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAIChatSubstrate requires a non-empty api_key")
        if not model:
            raise ValueError("OpenAIChatSubstrate requires a non-empty model")

        self.name = name
        self._model = model
        self._max_window_tokens = max_window_tokens
        self._default_params: dict[str, Any] = dict(default_params or {})
        self._deadline = Deadline.of(timeout_seconds)
        self._client = self._build_client(api_key=api_key, base_url=base_url)

    @staticmethod
    def _build_client(*, api_key: str, base_url: str | None) -> OpenAI:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - import-time guard
            raise ImportError(
                "OpenAIChatSubstrate requires the 'openai' extra: pip install 'dream[openai]'"
            ) from exc
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)

    # --- Substrate Protocol --------------------------------------------------

    def complete(self, prompt: str, params: dict[str, Any] | None = None) -> CompletionResult:
        merged = self._merged_params(params)
        max_tokens = int(merged.pop("max_tokens", 1024))
        model = str(merged.pop("model", None) or self._model)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": self._deadline.seconds,
            **token_limit_param(model, max_tokens),
            **merged,
        }
        with self._translating_timeouts():
            response = self._client.chat.completions.create(**kwargs)

        if not response.choices:
            raise RuntimeError(
                f"substrate {self.name!r} returned no choices for model {model!r} "
                "(possible content filter or malformed gateway response)"
            )
        choice = response.choices[0]
        text = (choice.message.content or "") if choice.message else ""
        usage = response.usage
        return CompletionResult(
            text=text,
            input_tokens=int(usage.prompt_tokens) if usage else 0,
            output_tokens=int(usage.completion_tokens) if usage else 0,
            finish_reason=str(choice.finish_reason or "stop"),
            raw={"id": response.id, "model": response.model},
        )

    def stream(self, prompt: str, params: dict[str, Any] | None = None) -> Iterator[str]:
        merged = self._merged_params(params)
        max_tokens = int(merged.pop("max_tokens", 1024))
        model = str(merged.pop("model", None) or self._model)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": self._deadline.seconds,
            "stream": True,
            **token_limit_param(model, max_tokens),
            **merged,
        }
        # Wrap the *iteration* too: OpenAI streams lazily, so a read timeout
        # surfaces while consuming ``stream`` (not at create()) — classify it
        # the same as a non-streaming timeout instead of leaking a raw error.
        with self._translating_timeouts():
            stream = self._client.chat.completions.create(**kwargs)
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    yield piece

    def count_tokens(self, text: str) -> int:
        return _approx_token_count(text)

    def max_window(self) -> int:
        return self._max_window_tokens

    def health(self) -> HealthReport:
        started = time.monotonic()
        try:
            # ``models.list`` is supported by vanilla OpenAI, Azure's /openai/v1
            # path, LiteLLM, vLLM, Ollama, and most gateways. Some Azure
            # deployments restrict it; in that case the probe surfaces
            # ``degraded`` rather than ``down`` so a working ``complete()``
            # path isn't pre-emptively benched.
            self._client.models.list(timeout=self._deadline.seconds)
        except Exception as exc:
            return HealthReport(
                state=self._classify_health(exc),
                detail=type(exc).__name__,
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        return HealthReport(state="ok", detail="", latency_ms=(time.monotonic() - started) * 1000.0)

    # --- internals -----------------------------------------------------------

    def _merged_params(self, overrides: dict[str, Any] | None) -> dict[str, Any]:
        merged = dict(self._default_params)
        if overrides:
            merged.update(overrides)
        return merged

    @contextlib.contextmanager
    def _translating_timeouts(self) -> Iterator[None]:
        """Run a block, translating any OpenAI SDK timeout into ``SubstrateTimeout``.

        Used by both ``complete`` and ``stream`` (the latter wraps the lazy
        iteration too, since a read timeout surfaces while consuming the stream).
        Non-timeout exceptions propagate unchanged.
        """
        try:
            yield
        except Exception as exc:
            self._reraise_timeout(exc)
            raise

    @staticmethod
    def _reraise_timeout(exc: BaseException) -> None:
        """Translate the OpenAI SDK's timeout into :class:`SubstrateTimeout`."""
        if "Timeout" in type(exc).__name__:
            raise SubstrateTimeout(str(exc)) from exc

    @staticmethod
    def _classify_health(exc: BaseException) -> HealthState:
        # 401/403 means the substrate is up but this credential is bad —
        # return ``degraded`` so failover considers credential rotation
        # before declaring the substrate itself down.
        name = type(exc).__name__
        if "Authentication" in name or "Permission" in name:
            return "degraded"
        if "Timeout" in name or "Connection" in name:
            return "down"
        return "degraded"
