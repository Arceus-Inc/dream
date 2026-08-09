"""Deterministic, provider-free comparison of harness variants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dream._immutable_json import FrozenJsonObject
from dream.observability import RunTrace, TraceEvent
from dream.replay import (
    DuplicateHarnessVariantIdError,
    DuplicateReplayCaseIdError,
    HarnessRevisionRef,
    HarnessVariant,
    IncompleteReplayResultError,
    InvalidReplayUsageError,
    ModelConfigRef,
    ReplayArtifactRef,
    ReplayAssertion,
    ReplayCase,
    ReplayComparator,
    ReplayExecution,
    ReplayIdentityMismatchError,
    ReplayOutcome,
    ReplayOutcomeKind,
    SandboxProfileRef,
    SkillRevisionRef,
    ToolProfileRef,
)
from dream.services.session_store import SCHEMA_VERSION, SessionCostSnapshot, SessionSnapshot


def _snapshot(
    session_id: str,
    *,
    input_tokens: int = 10,
    cost_usd: float = 0.1,
) -> SessionSnapshot:
    return SessionSnapshot(
        SCHEMA_VERSION,
        session_id,
        "test-model",
        None,
        SessionCostSnapshot(input_tokens, 4, 0, 0, cost_usd),
        (),
        (),
        datetime(2026, 8, 9, tzinfo=UTC),
    )


def _variant(variant_id: str) -> HarnessVariant:
    return HarnessVariant(
        variant_id=variant_id,
        revision=HarnessRevisionRef("harness-r1"),
        model_config=ModelConfigRef("test-model", "config-r1"),
        skill_revisions=(SkillRevisionRef("coding", "skill-r1"),),
        tool_profile=ToolProfileRef("tools-r1"),
        sandbox_profile=SandboxProfileRef("sandbox-r1"),
    )


def _execution(
    case_id: str,
    session: SessionSnapshot,
    outcome: ReplayOutcome,
    artifacts: tuple[ReplayArtifactRef, ...] = (),
    *,
    added_input_tokens: int = 0,
    added_cost_usd: float = 0.0,
    complete: bool = True,
) -> ReplayExecution:
    return ReplayExecution(
        case_id=case_id,
        session_snapshot=_snapshot(
            session.session_id,
            input_tokens=session.cost.input_tokens + added_input_tokens,
            cost_usd=session.cost.cost_usd + added_cost_usd,
        ),
        trace=RunTrace(session_id=session.session_id, events=()),
        outcome=outcome,
        artifacts=artifacts,
        complete=complete,
    )


class _Executor:
    def __init__(self, executions: tuple[ReplayExecution, ...]) -> None:
        self._executions = executions

    def __call__(self, case: ReplayCase, variant: HarnessVariant) -> ReplayExecution:
        for execution in self._executions:
            if execution.case_id == f"{variant.variant_id}:{case.case_id}":
                return ReplayExecution(
                    case_id=case.case_id,
                    session_snapshot=execution.session_snapshot,
                    trace=execution.trace,
                    outcome=execution.outcome,
                    artifacts=execution.artifacts,
                    complete=execution.complete,
                )
        raise AssertionError("missing scripted execution")


class _MismatchedExecutor:
    def __call__(self, case: ReplayCase, variant: HarnessVariant) -> ReplayExecution:
        return ReplayExecution(
            case_id="other-case",
            session_snapshot=case.session_snapshot,
            trace=RunTrace(session_id=case.session_snapshot.session_id, events=()),
            outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS),
            artifacts=(),
        )


class _SessionMismatchedExecutor:
    def __call__(self, case: ReplayCase, variant: HarnessVariant) -> ReplayExecution:
        return ReplayExecution(
            case_id=case.case_id,
            session_snapshot=_snapshot("other-session"),
            trace=RunTrace(session_id="other-session", events=()),
            outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS),
            artifacts=(),
        )


class _TraceEventMismatchedExecutor:
    def __call__(self, case: ReplayCase, variant: HarnessVariant) -> ReplayExecution:
        event = TraceEvent(
            ts="2026-08-09T12:00:00+00:00",
            session_id="other-session",
            task_id=None,
            event_type="state.transition",
            span_id="span-1",
            parent_span_id=None,
            attributes=FrozenJsonObject(),
        )
        return ReplayExecution(
            case_id=case.case_id,
            session_snapshot=case.session_snapshot,
            trace=RunTrace(session_id=case.session_snapshot.session_id, events=(event,)),
            outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS),
            artifacts=(),
        )


class _DecreasingUsageExecutor:
    def __call__(self, case: ReplayCase, variant: HarnessVariant) -> ReplayExecution:
        return ReplayExecution(
            case_id=case.case_id,
            session_snapshot=_snapshot(case.session_snapshot.session_id, input_tokens=9),
            trace=RunTrace(session_id=case.session_snapshot.session_id, events=()),
            outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS),
            artifacts=(),
        )


class _NonFiniteUsageExecutor:
    def __init__(self, cost_usd: float) -> None:
        self._cost_usd = cost_usd

    def __call__(self, case: ReplayCase, variant: HarnessVariant) -> ReplayExecution:
        return ReplayExecution(
            case_id=case.case_id,
            session_snapshot=_snapshot(
                case.session_snapshot.session_id,
                cost_usd=self._cost_usd,
            ),
            trace=RunTrace(session_id=case.session_snapshot.session_id, events=()),
            outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS),
            artifacts=(),
        )


def test_comparator_orders_cases_surfaces_noncritical_degradation_and_blocks_critical_failure() -> None:
    success = ReplayOutcome(ReplayOutcomeKind.SUCCESS)
    failure = ReplayOutcome(ReplayOutcomeKind.FAILURE)
    report = ReplayArtifactRef("report", "report-1")
    critical_session = _snapshot("critical-session")
    noncritical_session = _snapshot("noncritical-session")
    critical = ReplayCase(
        case_id="critical",
        session_snapshot=critical_session,
        assertions=(ReplayAssertion("must-succeed", True, expected_outcome=success),),
    )
    noncritical = ReplayCase(
        case_id="noncritical",
        session_snapshot=noncritical_session,
        assertions=(ReplayAssertion("report-visible", False, required_artifacts=(report,)),),
    )
    baseline = _variant("baseline")
    candidate = _variant("candidate")
    executor = _Executor(
        (
            _execution(
                "baseline:critical",
                critical_session,
                success,
                added_input_tokens=2,
                added_cost_usd=0.2,
            ),
            _execution(
                "candidate:critical",
                critical_session,
                failure,
                added_input_tokens=5,
                added_cost_usd=0.5,
            ),
            _execution("baseline:noncritical", noncritical_session, success, (report,)),
            _execution("candidate:noncritical", noncritical_session, success),
        )
    )

    comparison = ReplayComparator(executor).compare(
        baseline=baseline,
        candidate=candidate,
        cases=(critical, noncritical),
    )

    assert tuple(outcome.case_id for outcome in comparison.case_outcomes) == (
        "critical",
        "noncritical",
    )
    assert comparison.case_outcomes[0].usage_delta.input_tokens == 3
    assert comparison.case_outcomes[0].usage_delta.cost_usd == pytest.approx(0.3)
    assert comparison.case_outcomes[0].critical_regression is True
    assert comparison.case_outcomes[1].critical_regression is False
    assert comparison.case_outcomes[1].degraded is True
    assert comparison.critical_regression_case_ids == ("critical",)
    assert comparison.promotable is False


def test_comparator_rejects_duplicate_case_and_variant_ids() -> None:
    session = _snapshot("session")
    case = ReplayCase(
        case_id="case",
        session_snapshot=session,
        assertions=(ReplayAssertion("outcome", True, expected_outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS)),),
    )
    executor = _Executor(
        (
            _execution("baseline:case", session, ReplayOutcome(ReplayOutcomeKind.SUCCESS)),
            _execution("candidate:case", session, ReplayOutcome(ReplayOutcomeKind.SUCCESS)),
        )
    )
    comparator = ReplayComparator(executor)

    with pytest.raises(DuplicateReplayCaseIdError, match="duplicate replay case id: 'case'"):
        comparator.compare(
            baseline=_variant("baseline"),
            candidate=_variant("candidate"),
            cases=(case, case),
        )
    with pytest.raises(DuplicateHarnessVariantIdError, match="duplicate harness variant id: 'same'"):
        comparator.compare(
            baseline=_variant("same"),
            candidate=_variant("same"),
            cases=(case,),
        )
    with pytest.raises(ValueError, match="replay comparison requires at least one case"):
        comparator.compare(
            baseline=_variant("baseline"),
            candidate=_variant("candidate"),
            cases=(),
        )


def test_comparator_rejects_mismatched_and_incomplete_executor_results() -> None:
    session = _snapshot("session")
    case = ReplayCase(
        case_id="case",
        session_snapshot=session,
        assertions=(ReplayAssertion("outcome", True, expected_outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS)),),
    )
    baseline = _variant("baseline")
    candidate = _variant("candidate")
    with pytest.raises(ReplayIdentityMismatchError, match="returned case 'other-case' does not match 'case'"):
        ReplayComparator(_MismatchedExecutor()).compare(
            baseline=baseline,
            candidate=candidate,
            cases=(case,),
        )

    with pytest.raises(
        ReplayIdentityMismatchError,
        match="returned session 'other-session' does not match 'session'",
    ):
        ReplayComparator(_SessionMismatchedExecutor()).compare(
            baseline=baseline,
            candidate=candidate,
            cases=(case,),
        )

    with pytest.raises(IncompleteReplayResultError, match="incomplete replay result for case 'case'"):
        ReplayComparator(_Executor((
            _execution("baseline:case", session, ReplayOutcome(ReplayOutcomeKind.SUCCESS)),
            _execution(
                "candidate:case",
                session,
                ReplayOutcome(ReplayOutcomeKind.SUCCESS),
                complete=False,
            ),
        ))).compare(baseline=baseline, candidate=candidate, cases=(case,))

    with pytest.raises(
        ReplayIdentityMismatchError,
        match="trace event session 'other-session' does not match 'session'",
    ):
        ReplayComparator(_TraceEventMismatchedExecutor()).compare(
            baseline=baseline,
            candidate=candidate,
            cases=(case,),
        )

    with pytest.raises(
        InvalidReplayUsageError,
        match="replay cumulative usage must not decrease",
    ):
        ReplayComparator(_DecreasingUsageExecutor()).compare(
            baseline=baseline,
            candidate=candidate,
            cases=(case,),
        )


def test_replay_contract_rejects_blank_ids_and_duplicate_artifacts() -> None:
    artifact = ReplayArtifactRef("report", "report-1")
    with pytest.raises(ValueError, match="harness revision must not be empty"):
        HarnessRevisionRef("  ")
    with pytest.raises(
        ValueError,
        match="replay assertion artifacts must not contain duplicates",
    ):
        ReplayAssertion(
            assertion_id="report",
            critical=True,
            required_artifacts=(artifact, artifact),
        )
    with pytest.raises(
        ValueError,
        match="replay execution artifacts must not contain duplicates",
    ):
        ReplayExecution(
            case_id="case",
            session_snapshot=_snapshot("session"),
            trace=RunTrace(session_id="session", events=()),
            outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS),
            artifacts=(artifact, artifact),
        )


@pytest.mark.parametrize("cost_usd", (float("nan"), float("inf"), float("-inf")))
def test_comparator_rejects_non_finite_replay_cost(cost_usd: float) -> None:
    session = _snapshot("session")
    case = ReplayCase(
        case_id="case",
        session_snapshot=session,
        assertions=(
            ReplayAssertion(
                "outcome",
                True,
                expected_outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS),
            ),
        ),
    )

    with pytest.raises(
        InvalidReplayUsageError,
        match="replay cumulative cost must be finite",
    ):
        ReplayComparator(_NonFiniteUsageExecutor(cost_usd)).compare(
            baseline=_variant("baseline"),
            candidate=_variant("candidate"),
            cases=(case,),
        )


def test_comparator_rejects_non_finite_source_cost() -> None:
    session = _snapshot("session", cost_usd=float("nan"))
    case = ReplayCase(
        case_id="case",
        session_snapshot=session,
        assertions=(
            ReplayAssertion(
                "outcome",
                True,
                expected_outcome=ReplayOutcome(ReplayOutcomeKind.SUCCESS),
            ),
        ),
    )

    with pytest.raises(
        InvalidReplayUsageError,
        match="replay cumulative cost must be finite",
    ):
        ReplayComparator(_NonFiniteUsageExecutor(0.1)).compare(
            baseline=_variant("baseline"),
            candidate=_variant("candidate"),
            cases=(case,),
        )
