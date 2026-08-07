"""Role allow-list must filter the OpenAI ``tools`` wire schema.

A tool-less role (e.g. the output-schema reformatter) must not advertise the
full registry alongside ``response_format`` — providers reject that pairing
or the model attempts tools it cannot use.
"""

from __future__ import annotations

from dream._factory import _session_extra_params, _tool_advertised_to_model
from dream.api.response_format import ResponseFormat
from dream.session import SessionOptions


def _tool_wire(name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_tool_advertised_when_unscoped() -> None:
    assert _tool_advertised_to_model(name="bash", role_allowed=None) is True


def test_tool_advertised_only_when_in_allowlist() -> None:
    allowed: frozenset[str] = frozenset({"read_file"})
    assert _tool_advertised_to_model(name="read_file", role_allowed=allowed) is True
    assert _tool_advertised_to_model(name="bash", role_allowed=allowed) is False


def test_empty_allowlist_advertises_nothing() -> None:
    assert _tool_advertised_to_model(name="bash", role_allowed=frozenset()) is False


def test_session_extra_params_omits_tools_when_wire_empty() -> None:
    rf = ResponseFormat.json_object()
    options = SessionOptions(response_format=rf)
    extra = _session_extra_params([], options)
    assert extra is not None
    assert "tools" not in extra
    assert "tool_choice" not in extra
    assert extra["response_format"] == {"type": "json_object"}


def test_session_extra_params_keeps_tools_when_present() -> None:
    options = SessionOptions()
    extra = _session_extra_params([_tool_wire("bash")], options)
    assert extra is not None
    assert len(extra["tools"]) == 1
    assert extra["tool_choice"] == "auto"
