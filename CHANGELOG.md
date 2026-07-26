# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Open-source community files: `AGENTS.md`, spec-01 `docs/` tree, `SECURITY.md`,
  `CODE_OF_CONDUCT.md`, GitHub issue/PR templates, CI repo-structure check.
- `run_task` failure contract (chorus spec 05 §5): `dream.RunTaskError`
  (carries a typed `phase` of `plan`/`sprint`/`evaluate` plus the original
  `cause`) and `dream.TaskCancelled` (cooperative cancel). A fault inside the
  loop now surfaces as a typed `RunTaskError` naming where it broke;
  `asyncio.CancelledError` and `TaskCancelled` propagate untouched.
- `dream.contracts.__contract_version__` — the cross-repo contract version
  siblings assert against to fail fast on a drifted dream (chorus spec 05 §2).
- Initial repository scaffold: package tree, public API surface,
  cross-repo `dream.contracts` Protocols, test harness pinning the
  exported surface.
