"""Spec 06 — strict YAML frontmatter parsing for SKILL.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from dream.skills._frontmatter import (
    SkillFrontmatterError,
    parse_skill_meta,
    read_skill_body,
    read_skill_meta,
    split_frontmatter,
)
from tests.test_skills._helpers import write_skill


def test_split_frontmatter_separates_header_and_body() -> None:
    text = "---\nname: x\n---\nbody one\nbody two\n"
    header, body = split_frontmatter(text)
    assert "name: x" in header
    assert body.strip() == "body one\nbody two"


def test_split_frontmatter_without_fences_raises() -> None:
    with pytest.raises(SkillFrontmatterError):
        split_frontmatter("no frontmatter here")


def test_parse_minimal_required_fields() -> None:
    meta = parse_skill_meta(
        "name: refactor\ndescription: Refactor code.\nwhen_to_use: When messy.",
        source="bundled",
    )
    assert meta.name == "refactor"
    assert meta.description == "Refactor code."
    assert meta.when_to_use == "When messy."
    assert meta.source == "bundled"
    # documented defaults
    assert meta.risk == "safe"
    assert meta.user_invocable is True
    assert meta.disable_model_invocation is False
    assert meta.tools_required == ()
    assert meta.aliases == ()


def test_parse_full_contract_lists_and_bools() -> None:
    meta = parse_skill_meta(
        "\n".join(
            [
                "name: deploy",
                "description: Ship it.",
                "when_to_use: Releasing.",
                "tools_required: [bash, git]",
                "aliases: [release, ship]",
                "risk: caution",
                "user_invocable: false",
                "disable_model_invocation: true",
                "model: gpt-5",
                "argument_hint: <env>",
            ]
        ),
        source="project",
    )
    assert meta.tools_required == ("bash", "git")
    assert meta.aliases == ("release", "ship")
    assert meta.risk == "caution"
    assert meta.user_invocable is False
    assert meta.disable_model_invocation is True
    assert meta.model == "gpt-5"
    assert meta.argument_hint == "<env>"


@pytest.mark.parametrize("missing", ["name", "description", "when_to_use"])
def test_missing_required_key_raises(missing: str) -> None:
    fields = {"name": "x", "description": "y", "when_to_use": "z"}
    del fields[missing]
    yaml_text = "\n".join(f"{k}: {v}" for k, v in fields.items())
    with pytest.raises(SkillFrontmatterError):
        parse_skill_meta(yaml_text, source="user")


def test_blank_required_value_raises() -> None:
    with pytest.raises(SkillFrontmatterError):
        parse_skill_meta("name: x\ndescription:\nwhen_to_use: z", source="user")


def test_non_mapping_frontmatter_raises() -> None:
    with pytest.raises(SkillFrontmatterError):
        parse_skill_meta("- just\n- a\n- list", source="user")


def test_read_skill_meta_from_file(tmp_path: Path) -> None:
    path = write_skill(tmp_path, "refactor", description="Tidy code.", when_to_use="Messy.")
    meta = read_skill_meta(path, source="project")
    assert meta.name == "refactor"
    assert meta.description == "Tidy code."
    assert meta.source == "project"
    assert meta.path == path
    assert meta.base_dir == path.parent
    assert meta.command_name == "refactor"  # defaults to the skill dir name


def test_read_skill_body_returns_body_only(tmp_path: Path) -> None:
    path = write_skill(tmp_path, "refactor", body="line A\nline B")
    body = read_skill_body(path)
    assert "line A" in body
    assert "line B" in body
    assert "name: refactor" not in body  # frontmatter stripped
