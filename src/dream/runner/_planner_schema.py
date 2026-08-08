"""Typed planner response contract (native ``response_format`` schema).

Pydantic models are the source of truth; :data:`PLANNER_RESPONSE_SCHEMA` is
derived for the OpenAI wire. Domain artefacts remain
:class:`~dream.planner.PlannerLedger` / :class:`~dream.planner.LedgerStep`.

OpenAI/Azure ``strict: true`` requires every property key in ``required``.
Fields therefore have **no defaults** — optional wire values are explicit
nullables (``T | None``) or required strings/bools the model must emit.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from dream.api.response_format import JsonSchema

__all__ = [
    "PLANNER_RESPONSE_SCHEMA",
    "PlannerLedgerBody",
    "PlannerResponse",
    "PlannerStepBody",
]


class PlannerStepBody(BaseModel):
    """One planned step as emitted by the planner head."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    """What the evaluator will check for this step.

    Required: naming the work and naming its bar is one judgement, and asking
    for both here is what removes the propose/respond exchange from the sprint.
    """
    sprint_target: int | None
    notes: str


class PlannerLedgerBody(BaseModel):
    """Ledger fragment inside the planner JSON response."""

    model_config = ConfigDict(extra="forbid")

    steps: list[PlannerStepBody] = Field(min_length=1)
    evaluator_enabled: bool


class PlannerResponse(BaseModel):
    """Full planner head JSON object (replaces ``<spec>`` + ``<ledger>`` XML)."""

    model_config = ConfigDict(extra="forbid")

    spec_markdown: str = Field(min_length=1)
    ledger: PlannerLedgerBody


PLANNER_RESPONSE_SCHEMA: JsonSchema = JsonSchema.of(PlannerResponse.model_json_schema())
