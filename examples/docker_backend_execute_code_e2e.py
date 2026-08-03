"""Live e2e — DockerSandbox default + execute_code (keys from Chorus ``.env``).

Exercises the lean PR surface end-to-end:

1. Factory wires ``DockerSandbox`` by default (no sandbox.toml backend override)
2. Docker CLI/daemon availability
3. Real ``DockerSandbox.run`` echo when Docker is up
4. Live ``run_task`` using ``execute_code`` (Hermes-style tool-I/O collapse)
5. Live ``run_task`` bash routed through the Docker adapter (when Docker is up)

Credentials: loads ``../chorus/.env`` (or ``CHORUS_ENV_FILE``) for
``AZURE_OPENAI_API_KEY`` / ``AZURE_OPENAI_BASE_URL`` / ``AZURE_OPENAI_DEPLOYMENT``.

    cd /Users/divyansh/dream
    PYTHONPATH=src .venv/bin/python examples/docker_backend_execute_code_e2e.py

Skips cleanly (exit 0) when Azure keys are unset. Docker-dependent scenarios
are marked SKIP when the daemon is unavailable (exit 0 if non-docker checks
pass); set ``DREAM_E2E_REQUIRE_DOCKER=1`` to fail instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO, cast

from dream import SessionOptions, build_harness
from dream.runner import StdioObserver
from dream.sandbox import (
    SANDBOX_CONTEXT_KEY,
    DockerSandbox,
    SandboxAdapter,
    SandboxResult,
    get_docker_availability,
)

ECHO_MARKER = "DREAM-DOCKER-E2E-7K4P"
FILE_MARKER = "EXEC-CODE-E2E-9M2Q"

_CHORUS_ENV_CANDIDATES = (
    Path(os.environ["CHORUS_ENV_FILE"]) if os.environ.get("CHORUS_ENV_FILE") else None,
    Path("/Users/divyansh/chorus/.env"),
    Path(__file__).resolve().parents[2] / "chorus" / ".env",
)


def _load_env_file(path: Path) -> int:
    if not path.is_file():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            n += 1
    return n


def _load_chorus_azure() -> dict[str, str] | None:
    for candidate in _CHORUS_ENV_CANDIDATES:
        if candidate is None:
            continue
        loaded = _load_env_file(candidate)
        if loaded or candidate.is_file():
            print(f"[e2e] loaded env from {candidate} ({loaded} keys)", flush=True)
            break
    key = os.environ.get("AZURE_OPENAI_API_KEY")
    base = os.environ.get("AZURE_OPENAI_BASE_URL")
    dep = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
    if not (key and base and dep):
        return None
    return {"model": dep, "api_key": key, "base_url": base.rstrip("/")}


class _RecordingSandbox:
    """Wrap a real backend; log every command (oracle for adapter routing)."""

    def __init__(self, inner: SandboxAdapter, log: Path) -> None:
        self._inner = inner
        self._log = log
        self.calls: list[tuple[str, str, float]] = []

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 300.0,
    ) -> SandboxResult:
        self.calls.append((command, str(cwd), timeout_seconds))
        with self._log.open("a", encoding="utf-8") as fh:
            fh.write(f"{timeout_seconds}\t{cwd}\t{command}\n")
        return await self._inner.run(
            command, cwd=cwd, env=env, timeout_seconds=timeout_seconds
        )


class _Tee:
    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for s in self._streams:
            s.write(data)
        return len(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()


def _git_init(worktree: Path) -> None:
    def _run(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=worktree,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    _run("init", "-q", "-b", "main")
    _run("config", "user.email", "e2e@dream.local")
    _run("config", "user.name", "dream-e2e")
    _run("commit", "--allow-empty", "-q", "-m", "init")


def _fresh_workspace(home: Path, *, sandbox_toml: str = 'tier = "repo-write"\n') -> Path:
    wt = Path(tempfile.mkdtemp(prefix="e2e-docker-", dir=home))
    _git_init(wt)
    cfg = wt / ".harness" / "sandbox.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(sandbox_toml, encoding="utf-8")
    return wt


async def _run_task_capture(creds: dict[str, str], wt: Path, intent: str) -> str:
    buffer = io.StringIO()
    tee = _Tee(sys.stdout, buffer)
    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=wt,
    )
    async with harness:
        await harness.run_task(
            intent=intent,
            observer=StdioObserver(cast(TextIO, tee)),
            max_sprints=4,
        )
    return buffer.getvalue()


async def _run_task_capture_retry(
    creds: dict[str, str], wt: Path, intent: str, *, attempts: int = 3
) -> str:
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            return await _run_task_capture(creds, wt, intent)
        except Exception as exc:  # transient model/planner flake
            print(f"[retry] attempt {attempt}/{attempts} failed: {type(exc).__name__}: {exc}")
            last = f"{last}\n[error] {exc}"
    return last


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

    creds = _load_chorus_azure()
    if creds is None:
        print(
            "skipping: set AZURE_OPENAI_API_KEY / AZURE_OPENAI_BASE_URL / "
            "AZURE_OPENAI_DEPLOYMENT (via chorus/.env)"
        )
        return 0

    require_docker = os.environ.get("DREAM_E2E_REQUIRE_DOCKER", "").strip() in {
        "1",
        "true",
        "yes",
    }
    home = Path(tempfile.mkdtemp(prefix="e2e-docker-home-"))
    os.environ["DREAM_HOME"] = str(home / "dream")
    print(f"[e2e] model: {creds['model']} @ {creds['base_url']}", flush=True)
    print(f"[e2e] home:  {home}\n", flush=True)

    results: list[tuple[str, str, str]] = []  # name, PASS|FAIL|SKIP, detail

    # --- 1. default backend is DockerSandbox ---------------------------------
    wt1 = _fresh_workspace(home)
    harness = build_harness(
        model=creds["model"],
        api_key=creds["api_key"],
        base_url=creds["base_url"],
        working_dir=wt1,
    )
    engine = cast(Any, harness.config._engine_factory)("probe", SessionOptions())
    adapter = engine.dispatcher.context_metadata.get(SANDBOX_CONTEXT_KEY)
    ok1 = isinstance(adapter, DockerSandbox)
    results.append(
        (
            "1 default backend is DockerSandbox",
            "PASS" if ok1 else "FAIL",
            type(adapter).__name__,
        )
    )

    # --- 2. docker availability ----------------------------------------------
    avail = get_docker_availability()
    if avail.available:
        results.append(("2 docker CLI+daemon available", "PASS", avail.command or "docker"))
    else:
        tag = "FAIL" if require_docker else "SKIP"
        results.append(("2 docker CLI+daemon available", tag, avail.reason))

    # --- 3. real DockerSandbox.run -------------------------------------------
    if avail.available:
        assert isinstance(adapter, DockerSandbox)
        try:
            result = await adapter.run(f"echo {ECHO_MARKER}", cwd=wt1, timeout_seconds=60.0)
            ok3 = result.returncode == 0 and ECHO_MARKER in result.stdout
            results.append(
                (
                    "3 DockerSandbox.run echo",
                    "PASS" if ok3 else "FAIL",
                    f"rc={result.returncode} out={result.stdout.strip()!r} err={result.stderr.strip()!r}",
                )
            )
        finally:
            await adapter.stop()
    else:
        results.append(
            (
                "3 DockerSandbox.run echo",
                "FAIL" if require_docker else "SKIP",
                "docker unavailable",
            )
        )

    # --- 4. LIVE execute_code (no docker required for the Python child) ------
    # Force subprocess for bash nested tools so this scenario stays runnable
    # without Docker; the surface under test is execute_code collapse.
    print("\n[scenario 4] LIVE: execute_code tool-I/O collapse\n" + "-" * 60)
    wt4 = _fresh_workspace(
        home,
        sandbox_toml='tier = "repo-write"\nbackend = "subprocess"\n',
    )
    (wt4 / "seed.txt").write_text(f"seed={FILE_MARKER}\n", encoding="utf-8")
    intent4 = (
        "Use the execute_code tool once. In the script, import read_file and "
        "write_file from dream_tools, read seed.txt, and write result.txt with "
        f"exactly one line: got={FILE_MARKER}. Print 'done' to stdout. "
        "Do not use bash."
    )
    trace4 = await _run_task_capture_retry(creds, wt4, intent4)
    result_path = wt4 / "result.txt"
    body = result_path.read_text(encoding="utf-8") if result_path.is_file() else ""
    ok4 = FILE_MARKER in body and ("execute_code" in trace4 or result_path.is_file())
    results.append(
        (
            "4 LIVE execute_code wrote result.txt",
            "PASS" if ok4 else "FAIL",
            f"result={body.strip()!r}",
        )
    )

    # --- 5. LIVE bash through Docker adapter ---------------------------------
    print("\n[scenario 5] LIVE: bash via DockerSandbox\n" + "-" * 60)
    if avail.available:
        import dream._factory as factory

        wt5 = _fresh_workspace(home)  # default docker backend
        log = wt5 / "spy.log"
        inner = DockerSandbox()
        spy = _RecordingSandbox(inner, log)
        factory._select_sandbox_adapter = lambda _paths: spy  # type: ignore[assignment]
        intent5 = (
            f"Use the bash tool to run exactly this command and report its output: "
            f"echo {ECHO_MARKER}"
        )
        try:
            trace5 = await _run_task_capture_retry(creds, wt5, intent5)
            log_text = log.read_text(encoding="utf-8") if log.exists() else ""
            routed = bool(spy.calls) and ECHO_MARKER in log_text
            flowed = ECHO_MARKER in trace5 or ECHO_MARKER in log_text
            results.append(
                (
                    "5 LIVE bash routed through DockerSandbox",
                    "PASS" if (routed and flowed) else "FAIL",
                    f"calls={len(spy.calls)}",
                )
            )
        finally:
            await inner.stop()
    else:
        results.append(
            (
                "5 LIVE bash routed through DockerSandbox",
                "FAIL" if require_docker else "SKIP",
                "docker unavailable",
            )
        )

    # --- report --------------------------------------------------------------
    print("\n" + "=" * 60)
    fails = [n for n, tag, _ in results if tag == "FAIL"]
    skips = [n for n, tag, _ in results if tag == "SKIP"]
    for name, tag, detail in results:
        suffix = f"  ({detail})" if detail else ""
        print(f"[{tag}] {name}{suffix}")
    print("=" * 60)
    if fails:
        print(f"[e2e] {len(fails)} FAILURE(S): {fails}")
        return 1
    if skips:
        print(f"[e2e] {len(skips)} SKIPPED (install Docker to cover): {skips}")
    print("[e2e] non-failing — docker default + execute_code e2e complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
