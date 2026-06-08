"""Built-in destructive command-deny patterns (Spec 13A).

A conservative set of unambiguously dangerous shell invocations — root-targeted
recursive deletes, fork bombs, raw-disk writes, filesystem creation, and
piping a download straight into a shell. Operators extend the set via
``Policy.command_deny``; the checker searches both. Scoped deletes (``rm -rf
build/``) are intentionally NOT matched — only root-ish targets are.
"""

from __future__ import annotations

import re

BUILTIN_COMMAND_DENY: tuple[re.Pattern[str], ...] = (
    # Fork bomb: :(){ :|:& };:
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:"),
    # rm with both recursive and force flags targeting / /* ~ or $HOME
    re.compile(
        r"\brm\b(?=.*-\w*r)(?=.*-\w*f).*\s(?:/|/\*|~|\$HOME)(?:\s|$)",
        re.IGNORECASE,
    ),
    # dd writing to a raw block device
    re.compile(r"\bdd\b.*\bof=/dev/(?:sd|nvme|disk|hd)", re.IGNORECASE),
    # filesystem creation
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    # redirecting output onto a raw block device
    re.compile(r">\s*/dev/(?:sd|nvme|disk|hd)\w*", re.IGNORECASE),
    # piping a download straight into a shell
    re.compile(r"\b(?:curl|wget)\b.*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.IGNORECASE),
)
