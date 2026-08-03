"""Docker image availability and build helpers (OpenHarness-inspired).

Ensures the Dream sandbox image exists locally, optionally building from the
bundled Dockerfile next to this module.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

DEFAULT_IMAGE = "dream-sandbox:latest"

_DOCKERFILE_CONTENT = """\
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \\
    ripgrep bash git && \\
    rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash dreamuser
USER dreamuser
"""

__all__ = [
    "DEFAULT_IMAGE",
    "build_default_image",
    "ensure_image_available",
    "get_dockerfile_content",
]


def get_dockerfile_content() -> str:
    """Return the default Dockerfile content for the sandbox image."""
    return _DOCKERFILE_CONTENT


async def _image_exists(image: str) -> bool:
    docker = shutil.which("docker") or "docker"
    process = await asyncio.create_subprocess_exec(
        docker,
        "image",
        "inspect",
        image,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    return process.returncode == 0


async def build_default_image(image: str = DEFAULT_IMAGE) -> bool:
    """Build the default sandbox image from the bundled Dockerfile.

    Returns ``True`` on success, ``False`` on failure.
    """
    docker = shutil.which("docker") or "docker"
    dockerfile_path = Path(__file__).parent / "Dockerfile"


    if dockerfile_path.exists():
        process = await asyncio.create_subprocess_exec(
            docker,
            "build",
            "-t",
            image,
            "-f",
            str(dockerfile_path),
            str(dockerfile_path.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
    else:
        process = await asyncio.create_subprocess_exec(
            docker,
            "build",
            "-t",
            image,
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate(input=_DOCKERFILE_CONTENT.encode("utf-8"))

    return process.returncode == 0


async def ensure_image_available(image: str, auto_build: bool) -> bool:
    """Ensure the sandbox image exists, optionally building it.

    Returns ``True`` if the image is available.
    """
    if await _image_exists(image):
        return True
    if not auto_build:
        return False
    return await build_default_image(image)
