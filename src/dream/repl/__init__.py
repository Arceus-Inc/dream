"""Dev-only REPL harness — exercises the Spec 02 substrate + pool + failover surface.

Two subcommands, one terminal each:

    python -m dream.repl chat    # interactive prompt-driven dispatcher
    python -m dream.repl watch   # tail of the JSONL event log

The REPL is intentionally outside the public ``dream`` package re-exports
(``__init__.py``) — it's a developer tool, not part of the SDK contract.
"""
