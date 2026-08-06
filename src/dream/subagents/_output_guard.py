"""Runtime output-schema guardrail for subagents.

A subagent that declares an ``output_schema`` has its final message validated
against it after the beat: coerce → jsonschema → bounded repair with native
``response_format`` on the reformatter session. Default is fail-open (best-effort
+ warning); ``Subagent.strict`` fail-closes for DoD graders.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeGuard

import jsonschema

from dream.api.response_format import JsonSchema, resolve_structured_output
from dream.api.structured import JsonValue
from dream.roles._manifest import RoleManifest
from dream.session import SessionOptions

if TYPE_CHECKING:
    from dream.harness import Harness
    from dream.subagents._declaration import Subagent

MAX_OUTPUT_REPAIRS = 2


class OutputSchemaError(Exception):
    """Raised when a strict subagent cannot produce schema-valid output."""


def coerce_json(text: str) -> JsonValue | None:
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
    parsed = _loads_json_value(stripped)
    if parsed is not None:
        return parsed
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        return _loads_json_value(stripped[start : end + 1])
    return None


def validate_output(obj: JsonValue, schema: JsonSchema | Mapping[str, object]) -> list[str]:
    """Return human-readable schema-validation errors for ``obj`` ([] means valid)."""
    document = schema.document if isinstance(schema, JsonSchema) else schema
    validator = jsonschema.Draft202012Validator(document)
    return [
        f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in validator.iter_errors(obj)
    ]


def _loads_json_value(text: str) -> JsonValue | None:
    try:
        value: object = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if _is_json_value(value):
        return value
    return None


def _is_json_value(value: object) -> TypeGuard[JsonValue]:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


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


def _repair_prompt(text: str, *, schema: JsonSchema, errors: list[str]) -> str:
    return (
        "The text below must be a single JSON object matching the schema, but it failed validation.\n\n"
        f"## Schema\n{json.dumps(dict(schema.document))}\n\n"
        "## Validation errors\n- " + "\n- ".join(errors) + "\n\n"
        f"## Text to fix\n{text}\n\n"
        "Return ONLY the corrected JSON object. Do not add or invent data; only restructure what is "
        "present so it satisfies the schema."
    )


def _as_json_schema(schema: JsonSchema | Mapping[str, object]) -> JsonSchema:
    return schema if isinstance(schema, JsonSchema) else JsonSchema.of(schema)


async def enforce_output_schema(
    final_text: str,
    *,
    agent: Subagent,
    harness: Harness,
) -> tuple[str, str | None]:
    """Validate ``final_text`` against ``agent.output_schema``; repair, then open or closed.

    Returns ``(output, warning)`` on success / fail-open. Raises
    :class:`OutputSchemaError` when ``agent.strict`` and repairs are exhausted.
    """
    raw_schema = agent.output_schema
    if raw_schema is None:
        return final_text, None
    schema = _as_json_schema(raw_schema)

    text = final_text
    obj = coerce_json(text)
    errors = (
        validate_output(obj, schema) if obj is not None else ["<root>: output was not valid JSON"]
    )
    if not errors:
        return json.dumps(obj), None

    response_format = resolve_structured_output(
        schema=schema,
        name=f"{agent.name}_repair",
        strict=agent.strict,
    )
    manifest = _reformat_manifest()
    for _ in range(MAX_OUTPUT_REPAIRS):
        result = await harness.run_role(
            manifest,
            _repair_prompt(text, schema=schema, errors=errors),
            options=SessionOptions(max_turns=1, response_format=response_format),
        )
        text = result.final_text
        obj = coerce_json(text)
        errors = (
            validate_output(obj, schema)
            if obj is not None
            else ["<root>: output was not valid JSON"]
        )
        if not errors:
            return json.dumps(obj), None

    detail = "; ".join(errors[:3])
    if agent.strict:
        raise OutputSchemaError(
            f"output did not match schema after {MAX_OUTPUT_REPAIRS} repairs ({detail})"
        )

    best_effort = json.dumps(obj) if obj is not None else final_text
    warning = (
        f"⚠ output did not match the required schema after {MAX_OUTPUT_REPAIRS} repair attempts "
        f"({detail}); using best-effort result."
    )
    return best_effort, warning


__all__ = [
    "MAX_OUTPUT_REPAIRS",
    "OutputSchemaError",
    "coerce_json",
    "enforce_output_schema",
    "validate_output",
]
