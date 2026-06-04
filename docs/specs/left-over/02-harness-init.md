# Deferred — `harness init` repo initializer (PR 7)

**Origin:** pranjal-01 criteria 17–19. **Status:** deferred (split: `--no-ai` now, AI mode later).

## What it is

The bootstrapper that turns an **empty/near-empty repo into one that passes the
session-start validator** (`services/repo_validator.py`, PR 6). The validator
*checks* that `AGENTS.md` + the required `docs/` tree exist; the initializer is the
other half that *creates* them. Validator = the gate; initializer = the thing that
gets you through it. They are a pair.

## Two modes (both required by the spec)

1. **AI mode** — an LLM-driven agent reads whatever exists in the repo, asks the
   operator a few questions (project name, primary language, primary substrate),
   and writes a *tailored* `AGENTS.md`, `docs/design-docs/core-beliefs.md`,
   `docs/SECURITY.md`, and the required `docs/` folders.
2. **`--no-ai` mode** — a **deterministic** fallback that scaffolds a generic
   template **without contacting any model** (restricted / air-gapped
   environments). The operator then fills in `core-beliefs.md`.

Either way: commit the scaffold with a tagged message (`[harness:init]`), and a
subsequent session must pass the validator with no blocking findings.

## Required output (must satisfy PR 6's `_REQUIRED_PATHS`)

```
AGENTS.md                              # table of contents, ≤ 100 lines, links resolve
docs/design-docs/core-beliefs.md
docs/exec-plans/active/                # may be empty (.keep)
docs/product-specs/                    # may be empty (.keep)
docs/references/                       # may be empty (.keep)
docs/SECURITY.md
.gitignore                             # includes .dream/
```

## Acceptance criteria

- **MUST** ship an initializer that, given an empty repo, produces a validator-
  passing tree in one invocation.
- **MUST** offer a `--no-ai` mode that contacts no external model.
- **MUST** commit the scaffold with a tagged message.
- **SHOULD** keep `AGENTS.md` under the soft cap (100 lines) so it stays a table of
  contents, not a manual.

## Acceptance check

After `harness init` (either mode): `has_blocking(validate_repo(paths))` is `False`.

## Why deferred / split

- The **`--no-ai` half is easy** — write template files + a tagged commit. No
  dependencies, no model. It could ship **now** as a standalone PR and is a useful
  quick win (repos become bootstrappable before the engine exists).
- The **AI mode is blocked** — it needs the LLM provider (spec 02) + a minimal
  agent loop (spec 03). It cannot be fully built until those land. It is also the
  first real *consumer* of spec 02/03, so it doubles as an integration test once
  they exist.

**Recommendation:** ship `--no-ai` as a small standalone PR whenever bootstrappable
repos are wanted; build AI mode after spec 03.
