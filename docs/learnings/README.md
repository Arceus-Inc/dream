# Learnings

Durable, dated notes from running `dream` against real workloads — benchmark results,
failure modes we found, and the design conclusions we drew from them.

These are evidence, not marketing. Every number here is reproducible from the harness
checked in under [`datasets/`](../../datasets/); where a result is unflattering to `dream`
it is recorded as-is.

| Date | Note |
|------|------|
| 2026-07-26 | [SWE-bench Lite: dream vs opencode](2026-07-26-swe-bench-lite-vs-opencode.md) — 25 tasks, same model, official Docker grading |

## Adding a note

One file per study, named `YYYY-MM-DD-slug.md`. State the setup precisely enough that a
reader can rerun it, report the negative results, and end with what the evidence changes
about the design. Add a row to the table above.
