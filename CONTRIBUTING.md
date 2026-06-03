# Contributing to dream

`dream` is the SDK layer of the Arceus stack. It owns harness mechanics
and nothing else. If your change adds employees, org charts, channels,
budgets, OKRs, or memory curation, it belongs in `chorus`, `lattice`, or
`horizon` — not here.

## Setup

```pwsh
uv sync --all-extras
```

## Local checks

```pwsh
uv run ruff check src tests
uv run mypy
uv run pytest
```

## Pull request checklist

- One concern per PR. Small diffs review fast.
- Public API changes update `tests/test_public_api.py` and `CHANGELOG.md`.
- New behaviour ships with tests.
- No module-level mutable state. Pass dependencies through `HarnessConfig`
  or `register_*` methods.
- Private modules use a leading underscore (`_engine.py`) and stay out of
  `dream/__init__.py`.

## Boundary rule

If a feature only makes sense inside an organisation (employees,
delegation, governance, self-improvement, strategy), it does not belong
in this repo. Add the smallest extension point here that lets the
appropriate sibling repo build that feature on top.
