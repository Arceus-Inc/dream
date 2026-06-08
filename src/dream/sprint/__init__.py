"""Sprint orchestration primitives (spec 10 slice F).

Owns the contract artefact, bounded negotiation, evaluator record write
+ outcome→ledger transition, role-isolation lock, and evaluator on/off
resolution. The runner (slice 10-G) composes these into the
generator+evaluator loop; this package ships them as deterministic
units so each can be tested in isolation.
"""

from __future__ import annotations

from ._contract import (
    VALID_VERIFICATION_KINDS,
    NegotiationEntry,
    SprintContract,
    sprint_contract_path,
    tech_debt_path,
)
from ._disabling import is_evaluator_enabled_for_sprint
from ._evaluation import (
    EvaluationAlreadyRecorded,
    EvaluationOutcome,
    EvaluationRecord,
    evaluation_record_path,
    load_pending_carry_items,
    record_evaluation,
)
from ._generator import (
    StepNotPending,
    pick_next_pending_step,
    transition_step_to_in_progress,
)
from ._lock import RoleAlreadyActive, SprintRole, acquire_role_lock
from ._negotiation import (
    EvaluatorPropose,
    GeneratorRespond,
    NegotiationResult,
    build_contract_from_negotiation,
    negotiate_contract,
    negotiate_contract_async,
)
from ._outcome import append_tech_debt, apply_outcome

__all__ = [
    "VALID_VERIFICATION_KINDS",
    "EvaluationAlreadyRecorded",
    "EvaluationOutcome",
    "EvaluationRecord",
    "EvaluatorPropose",
    "GeneratorRespond",
    "NegotiationEntry",
    "NegotiationResult",
    "RoleAlreadyActive",
    "SprintContract",
    "SprintRole",
    "StepNotPending",
    "acquire_role_lock",
    "append_tech_debt",
    "apply_outcome",
    "build_contract_from_negotiation",
    "evaluation_record_path",
    "is_evaluator_enabled_for_sprint",
    "load_pending_carry_items",
    "negotiate_contract",
    "negotiate_contract_async",
    "pick_next_pending_step",
    "record_evaluation",
    "sprint_contract_path",
    "tech_debt_path",
    "transition_step_to_in_progress",
]
