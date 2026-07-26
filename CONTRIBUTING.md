# Contributing to dream

`dream` is the SDK layer of the Arceus stack. It owns harness mechanics
and nothing else. If your change adds employees, org charts, channels,
budgets, OKRs, or memory curation, it belongs in `chorus`, `lattice`, or
`horizon` — not here.

## Community

- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- [SECURITY.md](SECURITY.md) — report vulnerabilities privately, not via public issues

## Setup

```bash
uv sync --all-extras
```

## Local checks

```bash
uv run ruff check src tests
uv run mypy
uv run pytest
```

The repo itself must satisfy the session-start validator (spec 01):

```bash
uv run python -c "
from dream.config.paths import DreamPaths
from dream.services.repo_validator import has_blocking, validate_repo
paths = DreamPaths.resolve('.')
assert not has_blocking(validate_repo(paths)), validate_repo(paths)
print('repo structure OK')
"
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
