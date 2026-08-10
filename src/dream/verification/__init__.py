"""Verification — verify like a user, capture a report (Spec 12c).

Runs declared shell verification steps (the project's test runner + smoke
scripts), capturing each as a ``RepoVerificationStep``, and surfaces a UI
verification seam (``UiVerifier``) whose default skips. The real Playwright-MCP
UI verifier is a deferred leftover spec. The report feeds the evaluator (12d);
tech-debt auto-filing (12e) turns recognised failures into #07 tracker bullets.
"""

from __future__ import annotations

from dream.verification._config import (
    VerificationConfigError,
    parse_verification_config,
    read_verification_config,
)
from dream.verification._oracle import OracleResult, run_oracle
from dream.verification._report import write_report
from dream.verification._runner import run_verification
from dream.verification._tech_debt import (
    FilingResult,
    Matcher,
    TechDebtMatcherError,
    file_verification_tech_debt,
    load_matchers,
    match_failure,
    parse_matchers,
)
from dream.verification._types import (
    RepoVerificationStep,
    VerificationReport,
    VerificationStatus,
    VerificationStepSpec,
)
from dream.verification._ui import SkipUiVerifier, UiVerifier

__all__ = [
    "FilingResult",
    "Matcher",
    "OracleResult",
    "RepoVerificationStep",
    "SkipUiVerifier",
    "TechDebtMatcherError",
    "UiVerifier",
    "VerificationConfigError",
    "VerificationReport",
    "VerificationStatus",
    "VerificationStepSpec",
    "file_verification_tech_debt",
    "load_matchers",
    "match_failure",
    "parse_matchers",
    "parse_verification_config",
    "read_verification_config",
    "run_oracle",
    "run_verification",
    "write_report",
]
