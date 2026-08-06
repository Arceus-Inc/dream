"""Typed evaluator verdict contract (native ``response_format`` schema)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dream.api.response_format import JsonSchema

__all__ = [
    "EVALUATOR_VERDICT_SCHEMA",
    "EvaluatorVerdict",
    "VerdictOutcome",
]


class VerdictOutcome(StrEnum):
    """Durable evaluator outcomes (matches :data:`dream.sprint.EvaluationOutcome`)."""

    PASS = "pass"
    NEEDS_CHANGES = "needs-changes"
    FAIL = "fail"


class EvaluatorVerdict(BaseModel):
    """JSON object the evaluator head must emit."""

    model_config = ConfigDict(extra="forbid")

    outcome: VerdictOutcome
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str = ""
    items: list[str] = Field(default_factory=list)


EVALUATOR_VERDICT_SCHEMA: JsonSchema = JsonSchema.of(EvaluatorVerdict.model_json_schema())
