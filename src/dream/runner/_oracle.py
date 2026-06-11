"""Oracle execution — run the contract's verification steps for real (spec 15 P3 §1).

The evaluator LLM cannot run commands; before spec 15 it was shown the
contract's ``verification_steps`` as text and asked to judge — vibes.
The oracle closes that gap: the harness executes the steps itself
(subprocess, per-step timeout, via :func:`dream.verification.run_verification`)
and hands the evaluator *evidence*. The head then enforces the hard
rule: when verification steps exist, ``pass`` requires the oracle green.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dream.sprint import SprintContract
from dream.verification import (
    VerificationReport,
    VerificationStepSpec,
    run_verification,
)

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
