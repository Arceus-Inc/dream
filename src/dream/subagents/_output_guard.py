"""Runtime output-schema guardrail for subagents.

A subagent that declares an ``output_schema`` (a JSON-schema dict) has its final message validated
against it after the beat. dream has no typed-output seam, so the contract is enforced here: coerce the
final text to JSON, validate it against the schema, and on failure run a bounded, tool-less **reformat**
loop that only fixes the JSON structure (it never re-runs the research). If the output still cannot be
made valid, fail *open* — return the best-effort result with a warning, so the parent keeps working but
knows the contract was not fully met.

The pure pieces (:func:`coerce_json`, :func:`validate_output`) are model-free and unit-tested; the
:func:`enforce_output_schema` orchestrator drives the repair loop through the harness.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import jsonschema

from dream.roles._manifest import RoleManifest
from dream.session import SessionOptions

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.subagents._declaration import Subagent

# The child gets this many tool-less reformat passes to fix its JSON before we fail open.
MAX_OUTPUT_REPAIRS = 2


def coerce_json(text: str) -> Any | None:
    """Best-effort extract a JSON value from a model's final message; ``None`` if unparseable.

    Tolerates a leading/trailing ```` ```json ```` fence and surrounding prose by falling back to the
    outermost ``{...}`` span — models rarely emit a bare object even when told to.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        stripped = stripped.strip()
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def validate_output(obj: Any, schema: dict[str, Any]) -> list[str]:
    """Return human-readable schema-validation errors for ``obj`` ([] means valid)."""
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in validator.iter_errors(obj)
    ]


def _reformat_manifest() -> RoleManifest:
    """A tool-less role whose only job is to reshape text into schema-valid JSON."""
    return RoleManifest(
        name="subagent",
        description="json-reformatter",
        system_prompt=(
            "You repair JSON to satisfy a schema. Output ONLY the corrected JSON object — no prose, "
            "no code fences. Never invent or add data; only restructure what is present."
        ),
        system_prompt_mode="replace",
        tools=(),
        disallowed_tools=("spawn_subagent",),
        permission_mode="dontAsk",
        effort="low",
    )


def _repair_prompt(text: str, *, schema: dict[str, Any], errors: list[str]) -> str:
    return (
        "The text below must be a single JSON object matching the schema, but it failed validation.\n\n"
        f"## Schema\n{json.dumps(schema)}\n\n"
        "## Validation errors\n- " + "\n- ".join(errors) + "\n\n"
        f"## Text to fix\n{text}\n\n"
        "Return ONLY the corrected JSON object. Do not add or invent data; only restructure what is "
        "present so it satisfies the schema."
    )


async def enforce_output_schema(
    final_text: str,
    *,
    agent: Subagent,
    harness: Harness,
) -> tuple[str, str | None]:
    """Validate ``final_text`` against ``agent.output_schema``; repair-loop, then fail open.

    Returns ``(output, warning)``: on success ``output`` is canonical JSON and ``warning`` is ``None``;
    on exhausted repairs ``output`` is the best-effort result (canonical JSON if it at least parsed,
    else the original text) and ``warning`` is a note the parent surfaces.
    """
    schema = agent.output_schema
    if schema is None:  # defensive — caller only invokes when a schema is declared
        return final_text, None

    text = final_text
    obj = coerce_json(text)
    errors = validate_output(obj, schema) if obj is not None else ["<root>: output was not valid JSON"]
    if not errors:
        return json.dumps(obj), None

    manifest = _reformat_manifest()
    for _ in range(MAX_OUTPUT_REPAIRS):
        result = await harness.run_role(
            manifest, _repair_prompt(text, schema=schema, errors=errors),
            options=SessionOptions(max_turns=1),
        )
        text = result.final_text
        obj = coerce_json(text)
        errors = (
            validate_output(obj, schema) if obj is not None else ["<root>: output was not valid JSON"]
        )
        if not errors:
            return json.dumps(obj), None

    best_effort = json.dumps(obj) if obj is not None else final_text
    warning = (
        f"⚠ output did not match the required schema after {MAX_OUTPUT_REPAIRS} repair attempts "
        f"({'; '.join(errors[:3])}); using best-effort result."
    )
    return best_effort, warning


__all__ = ["MAX_OUTPUT_REPAIRS", "coerce_json", "enforce_output_schema", "validate_output"]
