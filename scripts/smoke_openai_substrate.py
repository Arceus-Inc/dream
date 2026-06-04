r"""Ad-hoc smoke test for OpenAIChatSubstrate against a live endpoint.

Reads creds from environment only. Not part of CI; run manually:

    $env:DREAM_SMOKE_API_KEY = "..."
    $env:DREAM_SMOKE_BASE_URL = "https://<resource>.cognitiveservices.azure.com/openai/v1"
    $env:DREAM_SMOKE_MODEL = "<deployment-name>"
    .\.venv\Scripts\python.exe scripts/smoke_openai_substrate.py
"""

from __future__ import annotations

import os
import sys

from dream.api.openai import OpenAIChatSubstrate


def main() -> int:
    api_key = os.environ.get("DREAM_SMOKE_API_KEY")
    base_url = os.environ.get("DREAM_SMOKE_BASE_URL")
    model = os.environ.get("DREAM_SMOKE_MODEL")
    if not (api_key and base_url and model):
        print("missing DREAM_SMOKE_API_KEY / _BASE_URL / _MODEL", file=sys.stderr)
        return 2

    sub = OpenAIChatSubstrate(
        name="azure_openai",
        api_key=api_key,
        model=model,
        base_url=base_url,
        max_window_tokens=128_000,
        timeout_seconds=30.0,
    )

    print(f"[health] -> {sub.health()}")

    result = sub.complete(
        "Reply with exactly the word PONG.",
        params={"max_tokens": 16},
    )
    print(f"[complete] text={result.text!r} in={result.input_tokens} out={result.output_tokens}")

    print("[stream] ", end="", flush=True)
    chunks = list(sub.stream("Count 1 to 3.", params={"max_tokens": 32}))
    print("".join(chunks))
    print(f"[stream chunks={len(chunks)}]")

    print(f"[count_tokens('hello world')] -> {sub.count_tokens('hello world')}")
    print(f"[max_window] -> {sub.max_window()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
