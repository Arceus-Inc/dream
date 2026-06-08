"""Tests for the file-based ``TeamRegistry``.

Teams live under ``<worktree>/.harness/swarm/teams/{team}/team.json`` — the
spec-10 worktree-as-record divergence from OpenHarness's
``~/.openharness/teams/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dream.swarm._identity import TeammateIdentity
from dream.swarm._registry import TeamFile, TeamMember, TeamRegistry


def _make_member(name: str = "planner", team: str = "alpha") -> TeamMember:
    ident = TeammateIdentity.create(name=name, team=team)
    return TeamMember(
        agent_id=ident.agent_id,
        name=ident.name,
        team=ident.team,
        backend_type="in_process",
        joined_at=1700000000.0,
        agent_type="planner",
        worktree_path=None,
    )


class TestTeamRegistryCreate:
    def test_create_team_writes_team_json_to_worktree(self, tmp_path: Path) -> None:
        reg = TeamRegistry(tmp_path)
        team = reg.create_team(name="Alpha Squad", description="researchers")

        team_file = tmp_path / ".harness" / "swarm" / "teams" / "alpha-squad" / "team.json"
        assert team_file.exists(), "team.json must live inside the worktree"
        loaded = json.loads(team_file.read_text(encoding="utf-8"))
        assert loaded["name"] == "alpha-squad"
        assert loaded["description"] == "researchers"
        assert team.name == "alpha-squad"

    def test_create_team_writes_no_file_outside_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spec-10 divergence: nothing under ``~/.openharness/`` ever."""
        sentinel = tmp_path / "fake-home" / ".openharness"
        monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "fake-home"))

        reg = TeamRegistry(tmp_path)
        reg.create_team(name="t1")

        assert not sentinel.exists()

    def test_create_team_rejects_duplicate(self, tmp_path: Path) -> None:
        reg = TeamRegistry(tmp_path)
        reg.create_team(name="t1")
        with pytest.raises(FileExistsError):
            reg.create_team(name="t1")

    def test_create_team_sanitises_name(self, tmp_path: Path) -> None:
        reg = TeamRegistry(tmp_path)
        team = reg.create_team(name="Foo Bar")
        assert team.name == "foo-bar"
        assert (
            tmp_path / ".harness" / "swarm" / "teams" / "foo-bar" / "team.json"
        ).exists()


class TestTeamRegistryMembership:
    def test_add_member_persists(self, tmp_path: Path) -> None:
        reg = TeamRegistry(tmp_path)
        reg.create_team(name="alpha")
        member = _make_member(name="planner", team="alpha")

        reg.add_member("alpha", member)
        team = reg.get_team("alpha")

        assert "planner@alpha" in team.members
        assert team.members["planner@alpha"].name == "planner"

    def test_remove_member_persists(self, tmp_path: Path) -> None:
        reg = TeamRegistry(tmp_path)
        reg.create_team(name="alpha")
        reg.add_member("alpha", _make_member())

        reg.remove_member("alpha", "planner@alpha")
        team = reg.get_team("alpha")
        assert "planner@alpha" not in team.members

    def test_get_team_missing_raises(self, tmp_path: Path) -> None:
        reg = TeamRegistry(tmp_path)
        with pytest.raises(FileNotFoundError):
            reg.get_team("nope")

    def test_list_teams_returns_sorted_names(self, tmp_path: Path) -> None:
        reg = TeamRegistry(tmp_path)
        reg.create_team(name="b-team")
        reg.create_team(name="a-team")
        reg.create_team(name="c-team")
        assert reg.list_teams() == ["a-team", "b-team", "c-team"]


class TestTeamFileRoundTrip:
    def test_to_dict_and_from_dict_round_trip(self) -> None:
        member = _make_member()
        original = TeamFile(
            name="alpha",
            description="x",
            created_at=1700000000.0,
            members={member.agent_id: member},
        )
        restored = TeamFile.from_dict(original.to_dict())
        assert restored == original

    def test_save_uses_atomic_write(self, tmp_path: Path) -> None:
        tf = TeamFile(name="alpha", description="", created_at=1.0)
        path = tmp_path / "team.json"
        tf.save(path)
        # No leftover .tmp file
        leftovers = list(tmp_path.glob("*.tmp"))
        assert not leftovers, f"atomic write left orphan tmp: {leftovers}"
        assert path.exists()
