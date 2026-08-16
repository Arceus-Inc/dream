"""Subagent catalogue for the system-prompt brief."""

from __future__ import annotations

from dream.subagents import EXPLORE, PLAN, VERIFY
from dream.subagents._catalogue import SubagentCatalogue
from dream.subagents._declaration import (
    GENERAL_PURPOSE_DESCRIPTION,
    GENERAL_PURPOSE_NAME,
    Subagent,
    SubagentSet,
)


def test_for_set_none_means_spawn_disabled() -> None:
    assert SubagentCatalogue.for_set(None) is None


def test_empty_set_lists_general_purpose_and_builtins() -> None:
    catalogue = SubagentCatalogue.for_set(SubagentSet())
    assert catalogue is not None
    assert [entry.name for entry in catalogue] == [
        GENERAL_PURPOSE_NAME,
        EXPLORE,
        PLAN,
        VERIFY,
    ]
    rendered = catalogue.render()
    assert "# Subagent definitions" in rendered
    assert GENERAL_PURPOSE_NAME in rendered
    assert GENERAL_PURPOSE_DESCRIPTION in rendered
    assert "spawn_subagent" not in rendered


def test_specialists_follow_builtins() -> None:
    catalogue = SubagentCatalogue.for_set(
        SubagentSet(
            agents={
                "reviewer": Subagent(
                    name="reviewer",
                    description="Reviews code\nextra detail ignored",
                    tools=("read_file",),
                ),
            }
        )
    )
    assert catalogue is not None
    assert [entry.name for entry in catalogue] == [
        GENERAL_PURPOSE_NAME,
        EXPLORE,
        PLAN,
        VERIFY,
        "reviewer",
    ]
    assert "- **reviewer** — Reviews code" in catalogue.render()


def test_role_override_of_explore_is_advertised() -> None:
    catalogue = SubagentCatalogue.for_set(
        SubagentSet(
            agents={
                EXPLORE: Subagent(
                    name=EXPLORE,
                    description="Role explore override",
                    tools=("read_file",),
                ),
            }
        )
    )
    assert catalogue is not None
    by_name = {entry.name: entry.description for entry in catalogue}
    assert by_name[EXPLORE] == "Role explore override"
