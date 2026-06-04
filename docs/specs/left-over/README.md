# Left-over / deferred work

Items that were consciously **deferred** during the spec-01 build (PRs 1–6). None
are required for spec 01 to be complete — they are enrichments, follow-ons, or
cross-cutting concerns that are cleaner to do later. Each note records *what it
is*, *why it was deferred*, a *design sketch*, and *effort*, so a future session
can pick it up cold.

| # | Item | Origin | Blocked on | Effort |
|---|---|---|---|---|
| [01](01-validator-plugins.md) | Per-repo validator plug-ins (downgrade-only + audit) | pranjal-01 criteria 20–21 (SHOULD) | nothing hard; overlaps spec 13 plugins | ~1 PR |
| [02](02-harness-init.md) | `harness init` repo initializer (AI + `--no-ai`) | pranjal-01 criteria 17–19 | AI mode needs spec 02/03 | `--no-ai` now; AI mode later |
| [03](03-async-migration.md) | Make the filesystem/git layer async | design decision during PR3 | nothing; optional until the engine lands | ~1 PR (wrappers) |

## How to use this folder

- These are **not** authoritative specs. The authority is `docs/specs/pranjal/`
  (conceptual) and `docs/specs/divo/` (build-order). These notes capture
  decisions and sketches so context isn't lost.
- When an item is picked up, build it as its own PR, move/expand the note into a
  real spec section if it grows, and delete the entry here when shipped.
