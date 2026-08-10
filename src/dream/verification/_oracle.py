"""Optional harness-side verification oracle (experimental).

Executes a sprint contract's ``verification_steps`` via
:func:`dream.verification.run_verification`. The production evaluator head
verifies in-session with ``bash`` instead; this module remains for callers
that want a sidecar evidence block.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.sprint import SprintContract
from dream.verification._runner import run_verification
from dream.verification._types import VerificationReport, VerificationStepSpec

__all__ = ["OracleResult", "run_oracle"]

_OUTPUT_TAIL_CHARS = 600


@dataclass(frozen=True)
class OracleResult:
    """The executed verification evidence for one sprint."""

    report: VerificationReport

    @property
    def green(self) -> bool:
        return not self.report.failures

    def failure_items(self) -> tuple[str, ...]:
        """Carry-item strings naming each failing command for the next sprint."""
        return tuple(
            f"verification step failed: {step.name or step.command} "
            f"(rc={step.returncode}): {_tail(step.stderr or step.stdout)}"
            for step in self.report.failures
        )

    def render_block(self) -> str:
        """The evidence block injected into the evaluator prompt."""
        lines = [
            "ORACLE RESULTS  (the harness EXECUTED the verification steps;",
            "trust this output over any other claim about test results)",
            "-" * 60,
        ]
        for step in self.report.steps:
            lines.append(f"- [{step.status}] {step.command} (rc={step.returncode})")
            if step.status in ("failed", "error"):
                output = _tail(step.stderr or step.stdout)
                if output:
                    lines.append(f"    output: {output}")
        verdict_rule = (
            "every step succeeded — a 'pass' outcome is permitted"
            if self.green
            else "at least one step FAILED — the outcome MUST NOT be 'pass'"
        )
        lines.append(f"Oracle verdict rule: {verdict_rule}.")
        return "\n".join(lines)


async def run_oracle(
    contract: SprintContract,
    *,
    cwd: Path,
    timeout_seconds: float = 300.0,
) -> OracleResult | None:
    """Execute the contract's runnable verification steps; ``None`` if it has none.

    Steps without a ``command`` (e.g. manual/UI kinds) are not runnable
    here and are left to the evaluator's judgement as before.
    """
    specs = [
        VerificationStepSpec(
            command=vs["command"], name=vs.get("kind", "")
        )
        for vs in contract.verification_steps
        if vs.get("command")
    ]
    if not specs:
        return None
    report = await run_verification(specs, cwd=cwd, timeout_seconds=timeout_seconds)
    return OracleResult(report)


def _tail(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= _OUTPUT_TAIL_CHARS:
        return cleaned
    return "…" + cleaned[-_OUTPUT_TAIL_CHARS:]
