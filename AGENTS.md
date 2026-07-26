# dream

Autonomous agent harness SDK (Python 3.11+). This repo is the **runtime layer** of the
[Arceus](https://github.com/Arceus-Inc) stack: one `Harness`, typed events, tools, hooks,
permissions, sandboxes, and the plan → sprint → evaluate task loop.

## Start here

| Doc | Purpose |
|-----|---------|
| [README.md](README.md) | Install, hello world, architecture overview |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, PR checklist, boundary rules |
| [consumer-facing-api/QUICKSTART.md](consumer-facing-api/QUICKSTART.md) | Zero to `run_task()` in five minutes |
| [consumer-facing-api/SDK_GUIDE.md](consumer-facing-api/SDK_GUIDE.md) | Full SDK guide |
| [docs/design-docs/core-beliefs.md](docs/design-docs/core-beliefs.md) | Non-negotiable design rules |
| [docs/specs/divo/00-architecture-and-build-order.md](docs/specs/divo/00-architecture-and-build-order.md) | Spec index and build order |

## Layout

```
src/dream/          Public API (`dream/__init__.py` is the contract)
tests/              Mirrors `src/dream/`; pins the exported surface
consumer-facing-api/  Guides and runnable examples for SDK users
docs/specs/divo/    Implementation specs (01–15)
examples/           Component demos
```

## Commands

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
uv run mypy
```

## Boundary

Features that only make sense inside an organisation (employees, delegation, OKRs, company
strategy) belong in sibling repos (`chorus`, `horizon`, `lattice`) — not here. Extend
`dream.contracts` when a sibling needs a typed seam; keep runtime deps out of contracts.

## Security

Report vulnerabilities per [SECURITY.md](SECURITY.md). Never commit API keys; use
[.env.example](.env.example) for local smoke config.
