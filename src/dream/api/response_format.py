"""Typed OpenAI-compat ``response_format`` (Hermes structured-output seam).

Domain objects — not bare dicts. Serialize with :meth:`ResponseFormat.to_openai`
only at the HTTP / adapter boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class JsonSchema:
    """JSON Schema document used for validation and ``json_schema`` format.

    Opaque by design (Draft 2020-12 is recursive); construct at the boundary
    from a mapping and pass this object everywhere else.
    """

    document: Mapping[str, object]

    @classmethod
    def of(cls, document: Mapping[str, object]) -> JsonSchema:
        return cls(document=document)


class ResponseFormatKind(StrEnum):
    """OpenAI ``response_format.type`` values we support."""

    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


@dataclass(frozen=True)
class JsonSchemaBinding:
    """Named schema binding for ``response_format.json_schema``."""

    name: str
    schema: JsonSchema
    strict: bool = False


@dataclass(frozen=True)
class ResponseFormat:
    """Structured-output constraint for a chat completion request."""

    kind: ResponseFormatKind
    json_schema: JsonSchemaBinding | None = None

    def __post_init__(self) -> None:
        if self.kind is ResponseFormatKind.JSON_SCHEMA and self.json_schema is None:
            raise ValueError("JSON_SCHEMA response format requires a json_schema binding")
        if self.kind is ResponseFormatKind.JSON_OBJECT and self.json_schema is not None:
            raise ValueError("JSON_OBJECT response format must not carry a schema binding")

    @classmethod
    def json_object(cls) -> ResponseFormat:
        return cls(kind=ResponseFormatKind.JSON_OBJECT)

    @classmethod
    def for_schema(
        cls,
        schema: JsonSchema | Mapping[str, object],
        *,
        name: str = "structured_output",
        strict: bool = False,
    ) -> ResponseFormat:
        doc = schema if isinstance(schema, JsonSchema) else JsonSchema.of(schema)
        return cls(
            kind=ResponseFormatKind.JSON_SCHEMA,
            json_schema=JsonSchemaBinding(name=name, schema=doc, strict=strict),
        )

    def to_openai(self) -> Mapping[str, object]:
        """Wire shape for the OpenAI-compat ``response_format`` field."""
        if self.kind is ResponseFormatKind.JSON_OBJECT:
            return {"type": self.kind.value}
        binding = self.json_schema
        if binding is None:  # pragma: no cover — guarded by __post_init__
            raise ValueError("JSON_SCHEMA response format missing binding")
        return {
            "type": self.kind.value,
            "json_schema": {
                "name": binding.name,
                "schema": dict(binding.schema.document),
                "strict": binding.strict,
            },
        }


def resolve_structured_output(
    *,
    schema: JsonSchema | Mapping[str, object] | None = None,
    json_mode: bool = False,
    name: str = "structured_output",
    strict: bool = False,
) -> ResponseFormat | None:
    """Map a schema / json_mode flag to a :class:`ResponseFormat`.

    Prefer ``json_schema`` when a schema is given; fall back to ``json_object``
    for json_mode-only; return ``None`` when neither is set.
    """
    if schema is not None:
        return ResponseFormat.for_schema(schema, name=name, strict=strict)
    if json_mode:
        return ResponseFormat.json_object()
    return None


__all__ = [
    "JsonSchema",
    "JsonSchemaBinding",
    "ResponseFormat",
    "ResponseFormatKind",
    "resolve_structured_output",
]
