"""Deterministic, execution-neutral comparison of harness variants."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from dream.observability import RunTrace
from dream.services.session_store import SessionCostSnapshot, SessionSnapshot

__all__ = [
    "DuplicateHarnessVariantIdError",
    "DuplicateReplayCaseIdError",
    "HarnessRevisionRef",
    "HarnessVariant",
    "IncompleteReplayResultError",
    "InvalidReplayUsageError",
    "ModelConfigRef",
    "ReplayArtifactRef",
    "ReplayAssertion",
    "ReplayAssertionOutcome",
    "ReplayCase",
    "ReplayCaseOutcome",
    "ReplayComparator",
    "ReplayComparison",
    "ReplayExecution",
    "ReplayExecutor",
    "ReplayIdentityMismatchError",
    "ReplayOutcome",
    "ReplayOutcomeKind",
    "SandboxProfileRef",
    "SessionCostDelta",
    "SkillRevisionRef",
    "ToolProfileRef",
]


class DuplicateReplayCaseIdError(ValueError):
    """Raised when a comparison contains the same replay case id twice."""


class DuplicateHarnessVariantIdError(ValueError):
    """Raised when baseline and candidate have the same variant id."""


class ReplayIdentityMismatchError(ValueError):
    """Raised when an executor result is not for the case being compared."""


class IncompleteReplayResultError(ValueError):
    """Raised when an executor explicitly reports an incomplete result."""


class InvalidReplayUsageError(ValueError):
    """Raised when an executor returns cumulative usage below the source snapshot."""


def _required(value: str, label: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} must not be empty")


@dataclass(frozen=True)
class HarnessRevisionRef:
    """Versioned identifier for a harness revision."""

    value: str

    def __post_init__(self) -> None:
        _required(self.value, "harness revision")


@dataclass(frozen=True)
class ModelConfigRef:
    """Pinned model and configuration revision for a harness variant."""

    model_id: str
    config_revision: str

    def __post_init__(self) -> None:
        _required(self.model_id, "model id")
        _required(self.config_revision, "model config revision")


@dataclass(frozen=True)
class SkillRevisionRef:
    """Pinned revision for one enabled skill."""

    skill_id: str
    revision: str

    def __post_init__(self) -> None:
        _required(self.skill_id, "skill id")
        _required(self.revision, "skill revision")


@dataclass(frozen=True)
class ToolProfileRef:
    """Pinned tool-profile revision."""

    value: str

    def __post_init__(self) -> None:
        _required(self.value, "tool profile")


@dataclass(frozen=True)
class SandboxProfileRef:
    """Pinned sandbox-profile revision."""

    value: str

    def __post_init__(self) -> None:
        _required(self.value, "sandbox profile")


@dataclass(frozen=True)
class HarnessVariant:
    """Immutable identity of one runnable harness configuration."""

    variant_id: str
    revision: HarnessRevisionRef
    model_config: ModelConfigRef
    skill_revisions: tuple[SkillRevisionRef, ...]
    tool_profile: ToolProfileRef
    sandbox_profile: SandboxProfileRef

    def __post_init__(self) -> None:
        _required(self.variant_id, "variant id")
        for index, skill in enumerate(self.skill_revisions):
            if any(skill.skill_id == prior.skill_id for prior in self.skill_revisions[:index]):
                raise ValueError(f"duplicate skill id: {skill.skill_id!r}")


class ReplayOutcomeKind(StrEnum):
    """Closed outcome kinds a replay executor may report."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ReplayOutcome:
    """Typed executor outcome; equality is the exact outcome criterion."""

    kind: ReplayOutcomeKind
    detail: str = ""


@dataclass(frozen=True)
class ReplayArtifactRef:
    """Typed, provider-neutral reference to an execution artifact."""

    kind: str
    reference: str

    def __post_init__(self) -> None:
        _required(self.kind, "artifact kind")
        _required(self.reference, "artifact reference")


@dataclass(frozen=True)
class ReplayAssertion:
    """One exact outcome and/or required-artifact replay criterion."""

    assertion_id: str
    critical: bool
    expected_outcome: ReplayOutcome | None = None
    required_artifacts: tuple[ReplayArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        _required(self.assertion_id, "assertion id")
        if self.expected_outcome is None and not self.required_artifacts:
            raise ValueError("replay assertion requires an outcome or artifact")
        if len(self.required_artifacts) != len(set(self.required_artifacts)):
            raise ValueError("replay assertion artifacts must not contain duplicates")


@dataclass(frozen=True)
class ReplayCase:
    """Immutable replay input, pinned to Dream's canonical session snapshot."""

    case_id: str
    session_snapshot: SessionSnapshot
    assertions: tuple[ReplayAssertion, ...]

    def __post_init__(self) -> None:
        _required(self.case_id, "case id")
        if not self.assertions:
            raise ValueError("replay case requires at least one assertion")
        for index, assertion in enumerate(self.assertions):
            if any(
                assertion.assertion_id == prior.assertion_id for prior in self.assertions[:index]
            ):
                raise ValueError(f"duplicate replay assertion id: {assertion.assertion_id!r}")


@dataclass(frozen=True)
class ReplayExecution:
    """One executor result, with only canonical session and trace read models."""

    case_id: str
    session_snapshot: SessionSnapshot
    trace: RunTrace
    outcome: ReplayOutcome
    artifacts: tuple[ReplayArtifactRef, ...]
    complete: bool = True

    def __post_init__(self) -> None:
        _required(self.case_id, "execution case id")
        if len(self.artifacts) != len(set(self.artifacts)):
            raise ValueError("replay execution artifacts must not contain duplicates")


class ReplayExecutor(Protocol):
    """Execution boundary; providers and harness construction stay behind it."""

    def __call__(self, case: ReplayCase, variant: HarnessVariant) -> ReplayExecution: ...


@dataclass(frozen=True)
class SessionCostDelta:
    """Signed candidate-minus-baseline delta over canonical session cost fields."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


@dataclass(frozen=True)
class ReplayAssertionOutcome:
    """Baseline/candidate evaluation of one replay assertion."""

    assertion_id: str
    critical: bool
    baseline_passed: bool
    candidate_passed: bool

    @property
    def regressed(self) -> bool:
        return self.baseline_passed and not self.candidate_passed


@dataclass(frozen=True)
class ReplayCaseOutcome:
    """Deterministic comparison result for one replay case."""

    case_id: str
    baseline: ReplayExecution
    candidate: ReplayExecution
    assertion_outcomes: tuple[ReplayAssertionOutcome, ...]
    usage_delta: SessionCostDelta
    degraded: bool
    critical_regression: bool
    candidate_critical_failure: bool


@dataclass(frozen=True)
class ReplayComparison:
    """Immutable comparison, ordered exactly as the supplied replay cases."""

    baseline: HarnessVariant
    candidate: HarnessVariant
    case_outcomes: tuple[ReplayCaseOutcome, ...]
    critical_regression_case_ids: tuple[str, ...]
    promotable: bool


class ReplayComparator:
    """Run the same ordered replay cases against two harness variants."""

    def __init__(self, executor: ReplayExecutor) -> None:
        self._executor = executor

    def compare(
        self,
        *,
        baseline: HarnessVariant,
        candidate: HarnessVariant,
        cases: tuple[ReplayCase, ...],
    ) -> ReplayComparison:
        _check_variant_ids(baseline, candidate)
        if not cases:
            raise ValueError("replay comparison requires at least one case")
        _check_case_ids(cases)
        baseline_executions = tuple(
            self._execute(case, baseline) for case in cases
        )
        candidate_executions = tuple(
            self._execute(case, candidate) for case in cases
        )
        outcomes = tuple(
            _compare_case(case, baseline_execution, candidate_execution)
            for case, baseline_execution, candidate_execution in zip(
                cases, baseline_executions, candidate_executions, strict=True
            )
        )
        critical_regression_case_ids = tuple(
            outcome.case_id for outcome in outcomes if outcome.critical_regression
        )
        promotable = not any(outcome.candidate_critical_failure for outcome in outcomes)
        return ReplayComparison(
            baseline=baseline,
            candidate=candidate,
            case_outcomes=outcomes,
            critical_regression_case_ids=critical_regression_case_ids,
            promotable=promotable,
        )

    def _execute(self, case: ReplayCase, variant: HarnessVariant) -> ReplayExecution:
        execution = self._executor(case, variant)
        _check_execution_identity(case, execution)
        return execution


def _check_variant_ids(baseline: HarnessVariant, candidate: HarnessVariant) -> None:
    if baseline.variant_id == candidate.variant_id:
        raise DuplicateHarnessVariantIdError(
            f"duplicate harness variant id: {baseline.variant_id!r}"
        )


def _check_case_ids(cases: tuple[ReplayCase, ...]) -> None:
    for index, case in enumerate(cases):
        if any(case.case_id == prior.case_id for prior in cases[:index]):
            raise DuplicateReplayCaseIdError(f"duplicate replay case id: {case.case_id!r}")


def _check_execution_identity(case: ReplayCase, execution: ReplayExecution) -> None:
    if not execution.complete:
        raise IncompleteReplayResultError(f"incomplete replay result for case {case.case_id!r}")
    if execution.case_id != case.case_id:
        raise ReplayIdentityMismatchError(
            f"returned case {execution.case_id!r} does not match {case.case_id!r}"
        )
    expected_session_id = case.session_snapshot.session_id
    if execution.session_snapshot.session_id != expected_session_id:
        raise ReplayIdentityMismatchError(
            "returned session "
            f"{execution.session_snapshot.session_id!r} does not match {expected_session_id!r}"
        )
    if execution.trace.session_id != expected_session_id:
        raise ReplayIdentityMismatchError(
            f"returned trace session {execution.trace.session_id!r} does not match {expected_session_id!r}"
        )
    for event in execution.trace.events:
        if event.session_id != expected_session_id:
            raise ReplayIdentityMismatchError(
                f"trace event session {event.session_id!r} does not match {expected_session_id!r}"
            )
    _check_cumulative_usage(case.session_snapshot.cost, execution.session_snapshot.cost)


def _check_cumulative_usage(
    source: SessionCostSnapshot,
    result: SessionCostSnapshot,
) -> None:
    if (
        result.input_tokens < source.input_tokens
        or result.output_tokens < source.output_tokens
        or result.cache_read_tokens < source.cache_read_tokens
        or result.cache_write_tokens < source.cache_write_tokens
        or result.cost_usd < source.cost_usd
    ):
        raise InvalidReplayUsageError("replay cumulative usage must not decrease")


def _compare_case(
    case: ReplayCase,
    baseline: ReplayExecution,
    candidate: ReplayExecution,
) -> ReplayCaseOutcome:
    assertion_outcomes = tuple(
        ReplayAssertionOutcome(
            assertion_id=assertion.assertion_id,
            critical=assertion.critical,
            baseline_passed=_assertion_passes(assertion, baseline),
            candidate_passed=_assertion_passes(assertion, candidate),
        )
        for assertion in case.assertions
    )
    return ReplayCaseOutcome(
        case_id=case.case_id,
        baseline=baseline,
        candidate=candidate,
        assertion_outcomes=assertion_outcomes,
        usage_delta=_usage_delta(case.session_snapshot.cost, baseline.session_snapshot.cost, candidate.session_snapshot.cost),
        degraded=any(outcome.regressed for outcome in assertion_outcomes),
        critical_regression=any(
            outcome.critical and outcome.regressed for outcome in assertion_outcomes
        ),
        candidate_critical_failure=any(
            outcome.critical and not outcome.candidate_passed for outcome in assertion_outcomes
        ),
    )


def _assertion_passes(assertion: ReplayAssertion, execution: ReplayExecution) -> bool:
    outcome_matches = (
        assertion.expected_outcome is None or execution.outcome == assertion.expected_outcome
    )
    artifacts_match = all(
        artifact in execution.artifacts for artifact in assertion.required_artifacts
    )
    return outcome_matches and artifacts_match


def _usage_delta(
    source: SessionCostSnapshot,
    baseline: SessionCostSnapshot,
    candidate: SessionCostSnapshot,
) -> SessionCostDelta:
    return SessionCostDelta(
        input_tokens=(candidate.input_tokens - source.input_tokens)
        - (baseline.input_tokens - source.input_tokens),
        output_tokens=(candidate.output_tokens - source.output_tokens)
        - (baseline.output_tokens - source.output_tokens),
        cache_read_tokens=(candidate.cache_read_tokens - source.cache_read_tokens)
        - (baseline.cache_read_tokens - source.cache_read_tokens),
        cache_write_tokens=(candidate.cache_write_tokens - source.cache_write_tokens)
        - (baseline.cache_write_tokens - source.cache_write_tokens),
        cost_usd=(candidate.cost_usd - source.cost_usd) - (baseline.cost_usd - source.cost_usd),
    )
