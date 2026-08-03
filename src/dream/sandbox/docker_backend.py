"""Docker sandbox backend — container execution behind ``SandboxAdapter``.

Inspired by OpenHarness's long-lived ``docker run`` + ``docker exec`` session:
one detached container per adapter instance (``--network none``, bind-mount the
working directory at the same host path, keep-alive via ``tail -f /dev/null``),
then each :meth:`DockerSandbox.run` is a timed ``docker exec``.

Docker is the default execution backend. Opt out with
``backend = "subprocess"`` in ``.harness/sandbox.toml``; it is never
inferred from the permission tier alone. When Docker is unavailable and
``fail_if_unavailable`` is false (the default), the factory soft-degrades
to ``SubprocessSandbox`` (Spec 13).
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import os
import platform
import shutil
import signal
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dream.errors import SandboxError
from dream.sandbox._adapter import SandboxResult
from dream.sandbox.docker_image import DEFAULT_IMAGE, ensure_image_available

__all__ = [
    "DockerAvailability",
    "DockerSandbox",
    "DockerSandboxConfig",
    "get_docker_availability",
]

_DEFAULT_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class DockerSandboxConfig:
    """Docker-specific settings (mirrors OpenHarness ``DockerSandboxSettings``)."""

    image: str = DEFAULT_IMAGE
    auto_build_image: bool = True
    cpu_limit: float = 0.0
    memory_limit: str = ""
    extra_mounts: tuple[str, ...] = ()
    extra_env: Mapping[str, str] = field(default_factory=dict)
    fail_if_unavailable: bool = False
    pids_limit: int = 256


@dataclass(frozen=True)
class DockerAvailability:
    """Whether the Docker CLI + daemon can run sandbox containers."""

    available: bool
    reason: str = ""
    command: str | None = None



async def _async_docker_exec(argv: list[str], *, timeout: float) -> int:
    """Run a docker CLI argv asynchronously; return exit code."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise
    return int(proc.returncode or 0)


def _sync_docker_exec(argv: list[str], *, timeout: float) -> int:
    """Sync wrapper for atexit / availability probes (no ``subprocess`` import)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_async_docker_exec(argv, timeout=timeout))
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            lambda: asyncio.run(_async_docker_exec(argv, timeout=timeout))
        ).result()


def get_docker_availability() -> DockerAvailability:
    """Check whether Docker can be used as a sandbox backend."""
    system = platform.system()
    release = platform.release().lower()
    on_wsl = bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in release
    if system == "Windows" and not on_wsl:
        return DockerAvailability(
            available=False,
            reason=(
                "Docker sandbox is not supported on native Windows; "
                "use WSL2 or Docker Desktop Linux engine"
            ),
        )
    docker = shutil.which("docker")
    if not docker:
        return DockerAvailability(
            available=False,
            reason="Docker CLI not found; install Docker Desktop or Docker Engine",
        )
    try:
        code = _sync_docker_exec([docker, "info"], timeout=5.0)
    except (TimeoutError, OSError):
        return DockerAvailability(
            available=False,
            reason="Docker daemon is not running",
            command=docker,
        )
    if code != 0:
        return DockerAvailability(
            available=False,
            reason="Docker daemon is not running",
            command=docker,
        )
    return DockerAvailability(available=True, command=docker)


@dataclass(eq=False)
class DockerSandbox:
    """Run shell commands inside a long-lived Docker container.

    The container is started lazily on the first :meth:`run` call, bind-mounting
    that call's ``cwd`` at the same absolute path. Later calls reuse the
    container when ``cwd`` stays under the mounted root; a cwd outside the
    mount triggers a restart with the new root.

    ``eq=False`` keeps instances identity-hashable so the weak debug registry
    can track live adapters without requiring a frozen/hashable value type.
    """

    config: DockerSandboxConfig = field(default_factory=DockerSandboxConfig)
    _container_name: str = field(init=False)
    _running: bool = field(init=False, default=False)
    _mount_cwd: Path | None = field(init=False, default=None)
    _atexit_registered: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._container_name = f"dream-sandbox-{uuid.uuid4().hex[:12]}"

    @property
    def container_name(self) -> str:
        return self._container_name

    @property
    def is_running(self) -> bool:
        return self._running

    def _docker_bin(self) -> str:
        return shutil.which("docker") or "docker"

    def _build_run_argv(self, cwd: Path) -> list[str]:
        """Build the ``docker run`` argv for container creation."""
        docker = self._docker_bin()
        cfg = self.config
        cwd_str = str(cwd.resolve())

        argv = [
            docker,
            "run",
            "-d",
            "--rm",
            "--name",
            self._container_name,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
        ]

        if cfg.pids_limit > 0:
            argv.extend(["--pids-limit", str(cfg.pids_limit)])

        if cfg.cpu_limit > 0:
            argv.extend(["--cpus", str(cfg.cpu_limit)])
        if cfg.memory_limit:
            argv.extend(["--memory", cfg.memory_limit])

        argv.extend(["-v", f"{cwd_str}:{cwd_str}"])
        argv.extend(["-w", cwd_str])

        for mount in cfg.extra_mounts:
            _validate_extra_mount(mount)
            argv.extend(["-v", mount])

        for key, value in cfg.extra_env.items():
            argv.extend(["-e", f"{key}={value}"])

        argv.extend([cfg.image, "tail", "-f", "/dev/null"])
        return argv

    def _cwd_on_mount(self, cwd: Path) -> bool:
        if self._mount_cwd is None:
            return False
        try:
            cwd.resolve().relative_to(self._mount_cwd.resolve())
            return True
        except ValueError:
            return cwd.resolve() == self._mount_cwd.resolve()

    async def _ensure_started(self, cwd: Path) -> None:
        availability = get_docker_availability()
        if not availability.available:
            msg = availability.reason or "Docker sandbox is unavailable"
            raise SandboxError(msg)

        resolved = cwd.resolve()
        if self._running and self._cwd_on_mount(resolved):
            return
        if self._running:
            await self.stop()

        available = await ensure_image_available(
            self.config.image, self.config.auto_build_image
        )
        if not available:
            raise SandboxError(
                f"Docker image {self.config.image!r} is not available and "
                "auto_build_image is disabled"
            )

        argv = self._build_run_argv(resolved)

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            msg = stderr.decode("utf-8", errors="replace").strip()
            raise SandboxError(f"Failed to start Docker sandbox: {msg}")

        self._running = True
        self._mount_cwd = resolved
        if not self._atexit_registered:
            atexit.register(self.stop_sync)
            self._atexit_registered = True
        from dream.sandbox._registry import register as _register_sandbox

        _register_sandbox(self)

    async def stop(self) -> None:
        """Stop and remove the sandbox container."""
        if not self._running:
            return
        docker = self._docker_bin()
        try:
            process = await asyncio.create_subprocess_exec(
                docker,
                "stop",
                "-t",
                "5",
                self._container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(process.communicate(), timeout=15)
        except (TimeoutError, OSError):
            pass
        finally:
            self._running = False
            self._mount_cwd = None

    def stop_sync(self) -> None:
        """Synchronous stop for use in atexit handlers."""
        if not self._running:
            return
        docker = self._docker_bin()
        try:
            _sync_docker_exec(
                [docker, "stop", "-t", "3", self._container_name],
                timeout=10.0,
            )
        except (TimeoutError, OSError):
            pass
        finally:
            self._running = False
            self._mount_cwd = None

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> SandboxResult:
        await self._ensure_started(cwd)

        docker = self._docker_bin()
        cmd: list[str] = [docker, "exec", "-w", str(cwd.resolve())]
        if env:
            for key, value in env.items():
                cmd.extend(["-e", f"{key}={value}"])
        cmd.extend([self._container_name, "bash", "-lc", command])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return SandboxResult(returncode=None, stderr=f"spawn failed: {exc}")

        try:
            async with asyncio.timeout(timeout_seconds):
                stdout, stderr = await proc.communicate()
        except TimeoutError:
            await _kill_tree(proc)
            return SandboxResult(
                returncode=None,
                stderr=f"timed out after {timeout_seconds}s",
                timed_out=True,
            )
        return SandboxResult(
            returncode=proc.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )


def _validate_extra_mount(mount: str) -> None:
    """Reject relative host paths in ``extra_mounts`` (escape hatch must be absolute)."""
    host = mount.split(":", 1)[0].strip()
    if not host:
        raise SandboxError(f"Invalid docker extra_mount {mount!r}: empty host path")
    if not Path(host).is_absolute():
        raise SandboxError(
            f"docker.extra_mounts entry {mount!r} must use an absolute host path"
        )


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    killpg = getattr(os, "killpg", None)
    if killpg is not None and proc.pid is not None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            killpg(os.getpgid(proc.pid), signal.SIGKILL)
    else:  # pragma: no cover - non-POSIX fallback
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()
