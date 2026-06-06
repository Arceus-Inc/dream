"""Spec 07 slice 1 — durable exec-plan ledger.

The ledger is the machine-readable JSON half of an exec-plan: it tracks
ordered ``LedgerEntry`` records as the plan advances. Three invariants
are load-bearing for autopilot (#11) and verification (#12) to consume
the ledger as a queue:

- single ``in_progress`` entry per task (Spec 07 decision 5),
- ``done`` requires ``passes=true`` when the plan has ``evaluator_enabled``
  (Spec 07 decision 5),
- ``notes`` are append-only (Spec 07 decision 5).

The ledger ships with a JSON Schema (``Ledger.json_schema()``) so the
session-start validator (Spec 01) can block on schema failures before any
generator/evaluator touches it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dream.tasks._ledger import (
    Ledger,
    LedgerEntry,
    LedgerSchemaError,
    LedgerStateError,
    read_ledger,
    write_ledger,
)


def _t() -> datetime:
    return datetime(2026, 6, 6, 12, 0, 0, tzinfo=UTC)


def _entry(
    *,
    id: str = "e1",
    status: str = "pending",
    passes: bool = False,
    notes: tuple[str, ...] = (),
) -> LedgerEntry:
    return LedgerEntry(
        id=id,
        description=f"do {id}",
        steps=(f"step a of {id}", f"step b of {id}"),
        status=status,  # type: ignore[arg-type]
        passes=passes,
        notes=notes,
    )


def _ledger(
    *,
    entries: tuple[LedgerEntry, ...] = (),
    evaluator_enabled: bool = False,
) -> Ledger:
    return Ledger(
        task_id="T1",
        state="active",
        created_at=_t(),
        updated_at=_t(),
        evaluator_enabled=evaluator_enabled,
        weights={},
        entries=entries,
    )


# --- shape ------------------------------------------------------------------


def test_ledger_entry_defaults() -> None:
    e = LedgerEntry(id="e1", description="d", steps=("s1",))
    assert e.status == "pending"
    assert e.passes is False
    assert e.notes == ()


def test_ledger_entry_is_frozen() -> None:
    e = _entry()
    with pytest.raises((AttributeError, TypeError)):
        setattr(e, "status", "done")


def test_ledger_is_frozen() -> None:
    led = _ledger(entries=(_entry(),))
    with pytest.raises((AttributeError, TypeError)):
        setattr(led, "state", "completed")


def test_ledger_entry_status_literal_rejected_at_construct() -> None:
    """Status must be one of the four literals; pydantic enforces."""
    with pytest.raises(Exception):  # ValidationError-ish
        LedgerEntry(id="e1", description="d", steps=("s1",), status="nope")  # type: ignore[arg-type]


def test_ledger_state_literal_rejected_at_construct() -> None:
    with pytest.raises(Exception):
        Ledger(
            task_id="T1",
            state="bogus",  # type: ignore[arg-type]
            created_at=_t(),
            updated_at=_t(),
            evaluator_enabled=False,
            weights={},
            entries=(),
        )


# --- single-in_progress invariant ------------------------------------------


def test_single_in_progress_allowed() -> None:
    led = _ledger(entries=(_entry(id="a", status="in_progress"),))
    assert led.in_progress_entry().id == "a"


def test_two_in_progress_rejected_at_construct() -> None:
    with pytest.raises(LedgerStateError, match="in_progress"):
        _ledger(
            entries=(
                _entry(id="a", status="in_progress"),
                _entry(id="b", status="in_progress"),
            )
        )


def test_in_progress_entry_returns_none_when_none() -> None:
    led = _ledger(entries=(_entry(id="a"), _entry(id="b", status="done", passes=True)))
    assert led.in_progress_entry() is None


# --- done requires passes under evaluator_enabled --------------------------


def test_done_without_pass_under_evaluator_enabled_is_rejected() -> None:
    with pytest.raises(LedgerStateError, match="passes"):
        _ledger(
            evaluator_enabled=True,
            entries=(_entry(id="a", status="done", passes=False),),
        )


def test_done_with_pass_under_evaluator_enabled_ok() -> None:
    led = _ledger(
        evaluator_enabled=True,
        entries=(_entry(id="a", status="done", passes=True),),
    )
    assert led.entries[0].status == "done"


def test_done_without_pass_under_evaluator_disabled_ok() -> None:
    """Without an evaluator there is nothing to enforce the verdict."""
    led = _ledger(
        evaluator_enabled=False,
        entries=(_entry(id="a", status="done", passes=False),),
    )
    assert led.entries[0].status == "done"


# --- notes append-only ------------------------------------------------------


def test_append_note_returns_new_ledger_with_note_appended() -> None:
    led = _ledger(entries=(_entry(id="a"),))
    led2 = led.append_note(entry_id="a", note="first note")
    assert led2.entries[0].notes == ("first note",)
    assert led.entries[0].notes == ()  # original untouched


def test_append_note_preserves_prior_notes() -> None:
    led = _ledger(entries=(_entry(id="a", notes=("n1",)),))
    led2 = led.append_note(entry_id="a", note="n2")
    led3 = led2.append_note(entry_id="a", note="n3")
    assert led3.entries[0].notes == ("n1", "n2", "n3")


def test_append_note_unknown_entry_raises() -> None:
    led = _ledger(entries=(_entry(id="a"),))
    with pytest.raises(LedgerStateError, match="unknown entry"):
        led.append_note(entry_id="missing", note="x")


# --- transition helpers -----------------------------------------------------


def test_mark_in_progress_sets_status_and_bumps_updated_at() -> None:
    led = _ledger(entries=(_entry(id="a"), _entry(id="b")))
    later = datetime(2026, 6, 7, tzinfo=UTC)
    led2 = led.mark_in_progress(entry_id="a", now=later)
    assert led2.entries[0].status == "in_progress"
    assert led2.entries[1].status == "pending"
    assert led2.updated_at == later


def test_mark_in_progress_refuses_when_other_in_progress() -> None:
    """Bookkeeping: try to flip a second entry while one is in_progress."""
    led = _ledger(entries=(_entry(id="a", status="in_progress"), _entry(id="b")))
    with pytest.raises(LedgerStateError, match="in_progress"):
        led.mark_in_progress(entry_id="b", now=_t())


def test_mark_done_requires_passes_under_evaluator_enabled() -> None:
    led = _ledger(
        evaluator_enabled=True,
        entries=(_entry(id="a", status="in_progress"),),
    )
    with pytest.raises(LedgerStateError, match="passes"):
        led.mark_done(entry_id="a", passes=False, now=_t())


def test_mark_done_with_passes_clears_in_progress_slot() -> None:
    led = _ledger(
        evaluator_enabled=True,
        entries=(_entry(id="a", status="in_progress"),),
    )
    led2 = led.mark_done(entry_id="a", passes=True, now=_t())
    assert led2.entries[0].status == "done"
    assert led2.entries[0].passes is True
    assert led2.in_progress_entry() is None


def test_mark_blocked_allowed_from_in_progress() -> None:
    led = _ledger(entries=(_entry(id="a", status="in_progress"),))
    led2 = led.mark_blocked(entry_id="a", now=_t())
    assert led2.entries[0].status == "blocked"


# --- serialisation ----------------------------------------------------------


def test_to_dict_roundtrip() -> None:
    led = _ledger(
        evaluator_enabled=True,
        entries=(
            _entry(id="a", status="done", passes=True, notes=("n1",)),
            _entry(id="b", status="in_progress"),
        ),
    )
    out = Ledger.model_validate(led.model_dump(mode="json"))
    assert out == led


def test_ledger_json_schema_emits_required_fields() -> None:
    """The ``$schema`` consumers (#01 session-start validator) need a real
    JSON Schema with required + property declarations."""
    schema = Ledger.json_schema()
    assert "properties" in schema
    for field in ("task_id", "state", "created_at", "updated_at", "entries"):
        assert field in schema["properties"]
    assert set(schema["required"]) >= {
        "task_id",
        "state",
        "created_at",
        "updated_at",
        "entries",
    }


def test_write_then_read_ledger(tmp_path: Path) -> None:
    led = _ledger(entries=(_entry(id="a"),))
    path = tmp_path / "T1.json"
    write_ledger(path, led)
    assert read_ledger(path) == led


def test_write_ledger_emits_schema_pointer(tmp_path: Path) -> None:
    """Every ledger on disk carries ``$schema`` so editors and the
    session-start validator agree which schema is in force."""
    led = _ledger(entries=(_entry(id="a"),))
    path = tmp_path / "T1.json"
    write_ledger(path, led)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "$schema" in raw
    assert raw["$schema"].endswith("exec-plan-ledger.schema.json")


def test_read_ledger_rejects_schema_violation(tmp_path: Path) -> None:
    """Schema failure at load time, not deep in pydantic-land — the
    Spec 01 validator wants a single classifiable error."""
    path = tmp_path / "T1.json"
    path.write_text(json.dumps({"task_id": "T1"}), encoding="utf-8")
    with pytest.raises(LedgerSchemaError):
        read_ledger(path)


def test_write_ledger_uses_atomic_helper(tmp_path: Path, monkeypatch) -> None:
    """Spec 01 decision 9 — repo-wide invariant test
    (``test_writes_route_through_atomic_helper``) already enforces this
    statically. The runtime check here keeps the unit story honest."""
    import dream.tasks._ledger as mod

    calls: list[Path] = []
    real = mod.atomic_write_text

    def spy(path, text, **kw):  # type: ignore[no-untyped-def]
        calls.append(Path(path))
        real(path, text, **kw)

    monkeypatch.setattr(mod, "atomic_write_text", spy)
    write_ledger(tmp_path / "T1.json", _ledger(entries=(_entry(id="a"),)))
    assert calls == [tmp_path / "T1.json"]
