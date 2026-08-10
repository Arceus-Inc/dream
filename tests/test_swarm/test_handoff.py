"""Spec 10 slice B — handoff event shape.

Pinned by spec 10 §"Handoff event":
- ``type = "handoff.{from}_to_{to}"`` (lowercase role names).
- ``ts`` is iso8601.
- ``from_role`` / ``to_role`` are documented role names.
- ``artefacts`` is a list of ``{kind, path|ref}`` with **at least one**
  pointer (repo-only rule: the next session locates inputs from these
  pointers, so an empty artefact list is structurally invalid).
"""

from __future__ import annotations

import pytest

from dream.swarm._handoff import HandoffArtefact, HandoffEvent, handoff_event


def test_handoff_type_string_matches_spec() -> None:
    ev = handoff_event(
        from_role="planner",
        to_role="generator",
        artefacts=[HandoffArtefact(kind="spec", path="docs/exec-plans/active/T1.md")],
    )
    assert isinstance(ev, HandoffEvent)
    assert ev.type == "handoff.planner_to_generator"


def test_handoff_carries_from_and_to_role_fields() -> None:
    ev = handoff_event(
        from_role="generator",
        to_role="evaluator",
        artefacts=[HandoffArtefact(kind="diff", path="sidecar://diff.patch")],
    )
    assert ev.from_role == "generator"
    assert ev.to_role == "evaluator"


def test_handoff_has_iso_timestamp() -> None:
    ev = handoff_event(
        from_role="planner",
        to_role="generator",
        artefacts=[HandoffArtefact(kind="spec", path="docs/exec-plans/active/T1.md")],
    )
    assert isinstance(ev.ts, str)
    # iso8601-ish: 'YYYY-MM-DDTHH:MM:SS...'
    assert "T" in ev.ts
    assert ev.ts.count("-") >= 2


def test_handoff_keeps_typed_artefacts() -> None:
    ev = handoff_event(
        from_role="planner",
        to_role="generator",
        artefacts=[
            HandoffArtefact(kind="spec", path="docs/exec-plans/active/T1.md"),
            HandoffArtefact(kind="ledger", path="docs/exec-plans/active/T1.json"),
        ],
    )
    assert ev.artefacts == (
        HandoffArtefact(kind="spec", path="docs/exec-plans/active/T1.md"),
        HandoffArtefact(kind="ledger", path="docs/exec-plans/active/T1.json"),
    )


def test_handoff_artefact_accepts_ref_instead_of_path() -> None:
    ev = handoff_event(
        from_role="generator",
        to_role="evaluator",
        artefacts=[HandoffArtefact(kind="diff", ref="sidecar://abc")],
    )
    [art] = ev.artefacts
    assert art == HandoffArtefact(kind="diff", ref="sidecar://abc")


def test_handoff_artefact_requires_path_or_ref() -> None:
    with pytest.raises(ValueError):
        HandoffArtefact(kind="spec")  # neither path nor ref


def test_handoff_rejects_empty_artefact_list() -> None:
    # The spec's "≥1 artefact" rule is structural — caught at event build time.
    with pytest.raises(ValueError):
        handoff_event(from_role="planner", to_role="generator", artefacts=[])


def test_handoff_rejects_unknown_role_name() -> None:
    with pytest.raises(ValueError):
        handoff_event(
            from_role="ceo",  # not in {runner, planner, generator, evaluator, reviewer}
            to_role="generator",
            artefacts=[HandoffArtefact(kind="spec", path="x")],
        )
