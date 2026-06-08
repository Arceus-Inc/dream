"""Tests for swarm teammate identity and name sanitisation."""

from __future__ import annotations

import pytest

from dream.swarm._identity import (
    TeammateIdentity,
    sanitize_agent_name,
    sanitize_team_name,
)


# --- sanitisers ----------------------------------------------------------


class TestSanitizeTeamName:
    """``sanitize_team_name`` — lowercase + replace non-alphanumerics with ``-``."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Researchers", "researchers"),
            ("docs team", "docs-team"),
            ("planner_v2", "planner-v2"),
            ("foo/bar", "foo-bar"),
            ("MixedCASE-123", "mixedcase-123"),
            ("a.b.c", "a-b-c"),
        ],
    )
    def test_sanitises_common_inputs(self, raw: str, expected: str) -> None:
        assert sanitize_team_name(raw) == expected

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            sanitize_team_name("")

    def test_rejects_input_that_becomes_empty_after_sanitisation(self) -> None:
        with pytest.raises(ValueError, match="must produce at least one alphanumeric"):
            sanitize_team_name("///")


class TestSanitizeAgentName:
    """``sanitize_agent_name`` — same as team plus ``@`` collapse.

    The ``@`` is forbidden because the agent_id format is
    ``{name}@{team}``; an agent name with an ``@`` would create an
    ambiguous id.
    """

    def test_replaces_at_sign(self) -> None:
        assert sanitize_agent_name("foo@bar") == "foo-bar"

    def test_lowercase_and_hyphenate(self) -> None:
        assert sanitize_agent_name("Planner V2") == "planner-v2"

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError):
            sanitize_agent_name("")


# --- TeammateIdentity ----------------------------------------------------


class TestTeammateIdentity:
    def test_agent_id_is_sanitised_name_at_team(self) -> None:
        ident = TeammateIdentity.create(name="Researcher", team="Docs Team")
        assert ident.agent_id == "researcher@docs-team"
        assert ident.name == "researcher"
        assert ident.team == "docs-team"

    def test_agent_id_uses_at_separator_after_sanitisation(self) -> None:
        # ``@`` in the raw name is sanitised away before composition
        ident = TeammateIdentity.create(name="foo@bar", team="t1")
        assert ident.agent_id == "foo-bar@t1"

    def test_identity_is_frozen(self) -> None:
        ident = TeammateIdentity.create(name="a", team="b")
        with pytest.raises(Exception):
            ident.agent_id = "other@x"  # type: ignore[misc]

    def test_identity_equality_by_value(self) -> None:
        a = TeammateIdentity.create(name="x", team="y")
        b = TeammateIdentity.create(name="X", team="Y")
        assert a == b
