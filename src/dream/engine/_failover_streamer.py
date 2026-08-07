"""``FailoverStreamer`` — credential pool + substrate rotation at the TurnStreamer seam.

Two-layer resilience (Spec 02):

- **Inner:** bounded retries on the same live credential (backoff / Retry-After cap).
- **Outer:** :class:`~dream.api.credentials.CredentialPool` benches dead keys; when the
  pool is empty, :class:`~dream.api.failover.FailoverPolicy` advances substrate.

Failures are classified once via :func:`~dream.api.error_classify.classify_failure`
so overflow compresses (never failovers) and auth benches without burning retries.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from dream.api.credentials import AttemptOutcome, Credential, CredentialPool, NoLiveCredential
from dream.api.error_classify import ClassifiedFailure, FailureKind, classify_failure
from dream.api.failover import FailoverPolicy, FailoverReason, NoLiveSubstrate
from dream.api.failover_events import EventCallback, RecoveryAttemptEvent
from dream.engine._events import StreamEvent
from dream.engine._loop import TurnStreamer
from dream.engine._messages import ConversationMessage
from dream.engine._retry_errors import CompressRequired
from dream.engine._substrate_slot import SubstrateSlot


class FailoverStreamer:
    """Wrap an ordered chain of :class:`SubstrateSlot`s with retry + failover."""

    def __init__(
        self,
        slots: Sequence[SubstrateSlot],
        *,
        retries_per_credential: int = 2,
        backoff_seconds: Sequence[float] = (1.0, 4.0),
        on_event: EventCallback | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not slots:
            raise ValueError("FailoverStreamer requires at least one substrate slot")
        names = [slot.name for slot in slots]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate substrate names in slots: {names}")
        self._slots = {slot.name: slot for slot in slots}
        self._policy = FailoverPolicy(order=list(names), on_event=on_event)
        self._on_event = on_event
        self._retries = retries_per_credential
        self._backoff = tuple(backoff_seconds)
        self._sleep = sleep
        self._coma_failovers = 0

    @classmethod
    def from_named_streamers(
        cls,
        streamers: Sequence[tuple[str, TurnStreamer]],
        *,
        retries_per_credential: int = 2,
        backoff_seconds: Sequence[float] = (1.0, 4.0),
        on_event: EventCallback | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> FailoverStreamer:
        """Build slots with a one-credential pool per named streamer (test helper)."""
        slots: list[SubstrateSlot] = []
        for name, streamer in streamers:
            pool = CredentialPool(
                name,
                (Credential(label="sole", key="unused", substrate=name),),
            )
            slots.append(SubstrateSlot(name=name, pool=pool, streamers={"sole": streamer}))
        return cls(
            slots,
            retries_per_credential=retries_per_credential,
            backoff_seconds=backoff_seconds,
            on_event=on_event,
            sleep=sleep,
        )

    def has_failover_target(self) -> bool:
        """True when the policy can advance past the active substrate."""
        active = self._policy.active()
        order = self._policy.order
        try:
            idx = order.index(active)
        except ValueError:
            return False
        return idx + 1 < len(order)

    def advance_after_coma(self) -> bool:
        """Spec 02 §14: on heartbeat coma, rotate once if a backup is live.

        Returns True when the active substrate advanced. Caps at one coma
        failover per streamer lifetime so flapping health cannot ping-pong.
        """
        if self._coma_failovers >= 1 or not self.has_failover_target():
            return False
        active = self._policy.active()
        try:
            self._policy.next_substrate(after=active, reason=FailoverReason.COMA)
        except NoLiveSubstrate:
            return False
        self._coma_failovers += 1
        return True

    async def stream_turn(
        self, messages: Sequence[ConversationMessage]
    ) -> AsyncIterator[StreamEvent]:
        last_error: BaseException | None = None
        while True:
            name = self._policy.active()
            slot = self._slots[name]
            try:
                async for event in self._stream_with_pool(slot, messages):
                    yield event
                return
            except CompressRequired:
                raise
            except _PoolExhausted as exc:
                last_error = exc.cause
                reason = exc.reason
            try:
                self._policy.next_substrate(after=name, reason=reason)
            except NoLiveSubstrate:
                raise NoLiveSubstrate(
                    f"failover chain exhausted after {name!r}; last error: {last_error!r}"
                ) from last_error

    async def _stream_with_pool(
        self,
        slot: SubstrateSlot,
        messages: Sequence[ConversationMessage],
    ) -> AsyncIterator[StreamEvent]:
        last_error: BaseException | None = None
        last_reason = FailoverReason.POOL_EXHAUSTED
        while True:
            try:
                cred = slot.pool.pick_live()
            except NoLiveCredential as exc:
                raise _PoolExhausted(cause=last_error or exc, reason=last_reason) from exc

            streamer = slot.streamers[cred.label]
            attempts = self._retries + 1
            for attempt in range(attempts):
                if attempt:
                    delay = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
                    await self._sleep(delay)
                yielded = False
                try:
                    async for event in streamer.stream_turn(messages):
                        yielded = True
                        yield event
                    slot.pool.record_attempt(cred.label, outcome=AttemptOutcome.SUCCESS)
                    return
                except BaseException as exc:
                    if yielded:
                        raise
                    last_error = exc
                    classified = classify_failure(exc)
                    last_reason = _reason_for(classified)
                    self._emit_recovery(slot.name, cred.label, classified)
                    if classified.should_compress:
                        raise CompressRequired(cause=exc, kind=classified.kind) from exc
                    if not classified.retryable:
                        slot.pool.record_attempt(cred.label, outcome=classified.outcome)
                        if classified.should_failover:
                            break
                        raise
                    if attempt + 1 >= attempts:
                        slot.pool.record_attempt(cred.label, outcome=classified.outcome)
                        break
                    if classified.backoff_seconds is not None:
                        await self._sleep(classified.backoff_seconds)

    def _emit_recovery(
        self, substrate: str, credential_label: str, classified: ClassifiedFailure
    ) -> None:
        if self._on_event is None:
            return
        action = _action_label(classified)
        self._on_event(
            RecoveryAttemptEvent(
                substrate=substrate,
                credential_label=credential_label,
                kind=classified.kind.value,
                action=action,
            )
        )


class _PoolExhausted(Exception):
    """Internal: active substrate has no live credentials left."""

    def __init__(self, *, cause: BaseException | None, reason: FailoverReason) -> None:
        super().__init__("substrate credential pool exhausted")
        self.cause = cause
        self.reason = reason


def _reason_for(classified: ClassifiedFailure) -> FailoverReason:
    if classified.kind is FailureKind.BILLING:
        return FailoverReason.BILLING
    if classified.kind is FailureKind.MODEL_NOT_FOUND:
        return FailoverReason.MODEL_NOT_FOUND
    if classified.outcome == AttemptOutcome.AUTH:
        return FailoverReason.AUTH
    if classified.outcome == AttemptOutcome.TRANSIENT_EXHAUSTED:
        return FailoverReason.TRANSIENT_EXHAUSTED
    return FailoverReason.POOL_EXHAUSTED


def _action_label(classified: ClassifiedFailure) -> str:
    if classified.should_compress:
        return "compress"
    if classified.retryable:
        return "retry"
    if classified.should_failover:
        return "failover"
    return "raise"


__all__ = ["FailoverStreamer"]
