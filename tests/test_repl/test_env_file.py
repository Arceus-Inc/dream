"""`_load_env_file` quote handling — only a matched surrounding pair is stripped."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dream.repl.__main__ import _load_env_file


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('K="value"', "value"),
        ("K='value'", "value"),
        ('K="\'inner\'"', "'inner'"),  # matched outer ", inner ' preserved
        ("K=plain", "plain"),
        ('K=un"matched', 'un"matched'),  # unmatched quote left intact
    ],
)
def test_load_env_file_quote_stripping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    key = raw.split("=", 1)[0]
    monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(raw + "\n", encoding="utf-8")
    n = _load_env_file(env_file)
    assert n == 1
    assert os.environ[key] == expected
    monkeypatch.delenv(key, raising=False)
