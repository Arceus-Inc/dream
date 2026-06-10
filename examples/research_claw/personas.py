"""Stage personas and per-stage instructions for the research pipeline.

One persona (the researcher) carries across all stages for voice; each
stage gets a tight instruction naming exactly the artifact it must leave
behind. The instructions are built from the workspace state so a stage
sees the prior stages' outputs.
"""

from __future__ import annotations

import json
from typing import Any

RESEARCHER_PERSONA = """\
You are a careful research scientist running one stage of an automated \
research pipeline. You work in a shared workspace: prior stages left \
markdown artifacts you can read, and you must leave this stage's artifact \
behind. Be rigorous and honest — never claim a result you did not measure.

You are given specific tools each stage. Use save_artifact to write \
markdown artifacts, write_file/read_file for code, run_experiment to \
execute experiment scripts. End the stage once its artifact is written.
"""

_SCOPE = """\
STAGE 1 — SCOPE. The research idea:

    {idea}

Frame it as a concrete, testable research problem. Write save_artifact \
name="problem.md" with: the problem statement, 1-2 precise hypotheses, \
and a SINGLE measurable success criterion an experiment could check \
(e.g. "method A reaches lower loss than B within N steps"). Keep the \
experiment cheap — runnable in seconds on a CPU, standard library or \
numpy only."""

_RELATED_WORK = """\
STAGE 2 — RELATED WORK. Read problem.md. Use arxiv_search (1-3 queries) \
to find genuinely related prior work. Write save_artifact \
name="related_work.md": 3-6 references, each with its arXiv link and one \
sentence on its relation to this problem. Cite only papers you actually \
retrieved."""

_EXPERIMENT = """\
STAGE 3 — EXPERIMENT. Read problem.md. Write a SELF-CONTAINED Python \
script that tests the hypothesis and, as its FINAL line, prints one JSON \
object of metrics (e.g. print(json.dumps({{"loss_a": ..., "loss_b": ..., \
"hypothesis_supported": true}}))).

Workflow:
1. write_file the script to "experiment.py" (stdlib or numpy only; must \
finish in well under a minute on CPU; set any random seed for \
reproducibility).
2. run_experiment path="experiment.py".
3. If it errors or prints no metrics JSON, read the traceback, fix the \
script, and run again — up to a few iterations.

Stop once run_experiment reports green WITH a metrics JSON. The script is \
the artifact; do not write prose here."""

_ANALYSIS = """\
STAGE 4 — ANALYSIS. The harness executed your experiment and recorded the \
AUTHORITATIVE results below (trust these over any earlier run):

{results_block}

Read problem.md. Write save_artifact name="analysis.md": what the numbers \
show, whether each hypothesis is supported by THIS data, effect sizes, and \
threats to validity. If the experiment did not run green, say so plainly \
and analyse only what is available."""

_PAPER = """\
STAGE 5 — PAPER. Read problem.md, related_work.md, analysis.md. The \
authoritative experiment results:

{results_block}

Write save_artifact name="paper.md": a complete short paper with sections \
Abstract, Introduction, Related Work, Method, Experiments (describe the \
exact experiment.py setup), Results (report the MEASURED numbers above \
verbatim — never invent), Discussion, Limitations, Conclusion, References \
(from related_work.md). {verification_note}"""

_REVIEW = """\
STAGE 6 — REVIEW. Read paper.md and results.json. Write save_artifact \
name="review.md": a short peer review checking (a) every reported number \
matches the authoritative results, (b) claims are supported by the \
experiment, (c) limitations are honest. Give a verdict: accept / \
revise / reject, with reasons."""


def _results_block(results: dict[str, Any] | None) -> str:
    if not results:
        return "(no experiment results were recorded)"
    return json.dumps(results, indent=2)


def stage_instruction(
    stage_name: str,
    *,
    idea: str,
    results: dict[str, Any] | None,
    experiment_verified: bool,
) -> str:
    """Build the user message that drives one stage's session."""
    if stage_name == "scope":
        return _SCOPE.format(idea=idea)
    if stage_name == "related_work":
        return _RELATED_WORK
    if stage_name == "experiment":
        return _EXPERIMENT
    block = _results_block(results)
    if stage_name == "analysis":
        return _ANALYSIS.format(results_block=block)
    if stage_name == "paper":
        note = (
            "The experiment ran green and is verified — you may state its "
            "conclusions as supported by experiment."
            if experiment_verified
            else "WARNING: the experiment did NOT produce verified green "
            "results. The paper MUST state this in Limitations and must not "
            "claim experimental support it does not have."
        )
        return _PAPER.format(results_block=block, verification_note=note)
    if stage_name == "review":
        return _REVIEW
    raise ValueError(f"unknown stage: {stage_name!r}")


# Which tools each stage's session needs, by tool name. The agent maps
# these onto registered tools.
STAGE_TOOLS: dict[str, tuple[str, ...]] = {
    "scope": ("save_artifact",),
    "related_work": ("arxiv_search", "save_artifact", "read_file"),
    "experiment": ("write_file", "read_file", "run_experiment"),
    "analysis": ("read_file", "save_artifact"),
    "paper": ("read_file", "save_artifact"),
    "review": ("read_file", "save_artifact"),
}

__all__ = ["RESEARCHER_PERSONA", "STAGE_TOOLS", "stage_instruction"]
