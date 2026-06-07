"""Spec 07 slice 1 — rolling append-only tech-debt tracker.

The harness writes findings here (verification failures, refactor-deviation
hits, doc-garden notes, manual operator entries) but **never acts on them
autonomously** — the operator triages by editing the file. Two properties
matter for that contract:

- new bullets are *appended*; existing bullets are never modified,
- every harness-filed bullet carries the five required fields:
  ``ts``, ``source``, ``task_id?``, ``missing``, ``evidence`` (Spec 07
  decision 6 / acceptance 8-10).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.tasks._tech_debt import (
    TechDebtEntry,
    TechDebtSource,
    append_tech_debt_entry,
    tech_debt_path,
)


def _t() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


# --- path & init ------------------------------------------------------------


def test_tech_debt_path(tmp_path: Path) -> None:
    """Lives at ``docs/exec-plans/tech-debt-tracker.md`` per spec."""
    root = tmp_path / "docs" / "exec-plans"
    assert tech_debt_path(root) == root / "tech-debt-tracker.md"


def test_append_to_missing_file_creates_it(tmp_path: Path) -> None:
    root = tmp_path / "docs" / "exec-plans"
    append_tech_debt_entry(
        root,
        TechDebtEntry(
            ts=_t(),
            source="verification.failure",
            task_id="T1",
            missing="missing rubric pass",
            evidence="docs/exec-plans/active/T1.json",
        ),
    )
    assert tech_debt_path(root).exists()


# --- required fields --------------------------------------------------------


def test_entry_requires_missing_and_evidence_and_source_and_ts() -> None:
    """All four non-optional fields are part of the contract — pydantic
    rejects a partial entry rather than letting an empty bullet land."""
    with pytest.raises(Exception):
        TechDebtEntry(ts=_t(), source="manual", missing="", evidence="")  # type: ignore[call-arg]


def test_entry_source_literal_enforced() -> None:
    with pytest.raises(Exception):
        TechDebtEntry(
            ts=_t(),
            source="not-a-known-source",  # type: ignore[arg-type]
            missing="x",
            evidence="y",
        )


def test_known_sources_match_spec() -> None:
    """Spec 07 §Tech-debt tracker lists these (plus an open-ended ``manual``)."""
    assert set(TechDebtSource.__args__) >= {  # type: ignore[attr-defined]
        "verification.failure",
        "refactor-deviation",
        "doc-garden",
        "manual",
    }


# --- append-only ------------------------------------------------------------


def test_append_adds_a_new_bullet_at_the_end(tmp_path: Path) -> None:
    root = tmp_path / "docs" / "exec-plans"
    append_tech_debt_entry(
        root,
        TechDebtEntry(
            ts=_t(),
            source="verification.failure",
            task_id="T1",
            missing="rubric did not pass",
            evidence="ledger://T1#a",
        ),
    )
    append_tech_debt_entry(
        root,
        TechDebtEntry(
            ts=_t(),
            source="doc-garden",
            task_id=None,
            missing="README out of date",
            evidence="README.md",
        ),
    )
    body = tech_debt_path(root).read_text(encoding="utf-8")
    # Two bullets, in order
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert len(bullets) == 2
    assert "rubric did not pass" in bullets[0]
    assert "README out of date" in bullets[1]


def test_append_preserves_existing_operator_content(tmp_path: Path) -> None:
    """Operator triages by editing — appends must not touch what's there."""
    root = tmp_path / "docs" / "exec-plans"
    path = tech_debt_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Tech debt tracker\n\n- (operator) reviewed; still real\n",
        encoding="utf-8",
    )
    append_tech_debt_entry(
        root,
        TechDebtEntry(
            ts=_t(),
            source="manual",
            task_id=None,
            missing="new thing",
            evidence="hand-filed",
        ),
    )
    body = path.read_text(encoding="utf-8")
    assert "(operator) reviewed; still real" in body
    assert "new thing" in body
    # operator bullet still the first one
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert bullets[0] == "- (operator) reviewed; still real"


def test_appended_bullet_includes_all_required_fields(tmp_path: Path) -> None:
    root = tmp_path / "docs" / "exec-plans"
    append_tech_debt_entry(
        root,
        TechDebtEntry(
            ts=_t(),
            source="verification.failure",
            task_id="T1",
            missing="rubric pass missing",
            evidence="ledger://T1#a",
        ),
    )
    body = tech_debt_path(root).read_text(encoding="utf-8")
    line = next(line for line in body.splitlines() if line.startswith("- "))
    # ISO 8601 ts
    assert "2026-06-06T12:00:00" in line
    assert "verification.failure" in line
    assert "T1" in line
    assert "rubric pass missing" in line
    assert "ledger://T1#a" in line


def test_appended_bullet_omits_task_id_when_none(tmp_path: Path) -> None:
    """``task_id`` is optional — formatter shouldn't write ``task_id=None``."""
    root = tmp_path / "docs" / "exec-plans"
    append_tech_debt_entry(
        root,
        TechDebtEntry(
            ts=_t(),
            source="doc-garden",
            task_id=None,
            missing="stale ref",
            evidence="docs/references/x.md",
        ),
    )
    body = tech_debt_path(root).read_text(encoding="utf-8")
    assert "None" not in body


def test_two_failures_in_one_session_append_two_bullets(tmp_path: Path) -> None:
    """Acceptance scenario: tech-debt tracker append-only by harness."""
    root = tmp_path / "docs" / "exec-plans"
    for i in range(2):
        append_tech_debt_entry(
            root,
            TechDebtEntry(
                ts=_t(),
                source="verification.failure",
                task_id="T1",
                missing=f"thing {i}",
                evidence="ledger://T1#a",
            ),
        )
    body = tech_debt_path(root).read_text(encoding="utf-8")
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert len(bullets) == 2


# --- markdown injection hardening (#54) ------------------------------------


@pytest.mark.parametrize("bad", ["line1\nline2", "line1\rline2", "x\n## fake"])
def test_missing_field_rejects_newlines(bad: str) -> None:
    """A newline in ``missing`` would inject fake bullets/sections."""
    with pytest.raises(Exception, match="single line"):
        TechDebtEntry(ts=_t(), source="manual", missing=bad, evidence="y")


def test_evidence_field_rejects_newlines() -> None:
    with pytest.raises(Exception, match="single line"):
        TechDebtEntry(
            ts=_t(), source="manual", missing="x", evidence="a\n- injected"
        )


def test_task_id_field_rejects_newlines() -> None:
    with pytest.raises(Exception, match="single line"):
        TechDebtEntry(
            ts=_t(),
            source="manual",
            missing="x",
            evidence="y",
            task_id="T1\n## Heading",
        )


def test_bullet_stays_single_line_for_valid_entry() -> None:
    """Sanity: a well-formed entry renders to exactly one line."""
    bullet = TechDebtEntry(
        ts=_t(), source="manual", missing="x", evidence="y"
    ).to_bullet()
    assert "\n" not in bullet
    assert "\r" not in bullet


# --- concurrency: lock-serialised append (#55) -----------------------------


def test_append_is_lock_serialised(tmp_path: Path, monkeypatch) -> None:
    """The read-modify-write must hold an exclusive lock so concurrent
    writers can't both read the same prior content and lose an append."""
    import dream.tasks._tech_debt as mod

    events: list[str] = []
    real_lock = mod.exclusive_file_lock

    from contextlib import contextmanager

    @contextmanager
    def spy_lock(path, **kw):  # type: ignore[no-untyped-def]
        events.append(f"lock:{Path(path).name}")
        with real_lock(path, **kw):
            yield
        events.append(f"unlock:{Path(path).name}")

    real_write = mod.atomic_write_text

    def spy_write(path, text, **kw):  # type: ignore[no-untyped-def]
        events.append("write")
        real_write(path, text, **kw)

    monkeypatch.setattr(mod, "exclusive_file_lock", spy_lock)
    monkeypatch.setattr(mod, "atomic_write_text", spy_write)

    root = tmp_path / "docs" / "exec-plans"
    append_tech_debt_entry(
        root,
        TechDebtEntry(ts=_t(), source="manual", missing="x", evidence="y"),
    )
    # The write must happen strictly inside the lock window.
    assert events[0].startswith("lock:")
    assert events[-1].startswith("unlock:")
    assert events.index("write") < events.index(events[-1])


def test_concurrent_appends_all_land(tmp_path: Path) -> None:
    """Threads appending in parallel must not clobber each other (lock holds
    each read+write together)."""
    import threading

    root = tmp_path / "docs" / "exec-plans"
    n = 12

    def worker(i: int) -> None:
        append_tech_debt_entry(
            root,
            TechDebtEntry(
                ts=_t(), source="manual", missing=f"finding-{i}", evidence="e"
            ),
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    body = tech_debt_path(root).read_text(encoding="utf-8")
    bullets = [line for line in body.splitlines() if line.startswith("- ")]
    assert len(bullets) == n
    for i in range(n):
        assert f"finding-{i}" in body


def test_append_uses_atomic_helper(tmp_path: Path, monkeypatch) -> None:
    """Spec 01 decision 9 — append goes through ``atomic_write_text``
    (read-modify-write atomic swap), not ``open(..., "a")``."""
    import dream.tasks._tech_debt as mod

    calls: list[Path] = []
    real = mod.atomic_write_text

    def spy(path, text, **kw):  # type: ignore[no-untyped-def]
        calls.append(Path(path))
        real(path, text, **kw)

    monkeypatch.setattr(mod, "atomic_write_text", spy)
    root = tmp_path / "docs" / "exec-plans"
    append_tech_debt_entry(
        root,
        TechDebtEntry(
            ts=_t(),
            source="manual",
            task_id=None,
            missing="x",
            evidence="y",
        ),
    )
    assert calls == [tech_debt_path(root)]
