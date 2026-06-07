"""CodeAnt #12/#14 lock-in — BaseTool subclass validation.

#12: ``input_model`` MUST be a ``pydantic.BaseModel`` subclass; a non-model
must be rejected at class creation (not crash later in input_schema()).
#14: validation must NOT be bypassed by a concrete subclass that inherits
``execute`` instead of defining its own — abstractness, not ``__dict__``
membership, is the correct gate.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from dream.tools._base import BaseTool, ToolDeclaration, ToolDeclarationError
from dream.tools._context import ToolExecutionContext


class _GoodInput(BaseModel):
    x: int


def _make_concrete_base() -> type[BaseTool]:
    """A concrete intermediate that defines execute, so children can inherit it."""

    class _Concrete(BaseTool):
        name = "concrete"
        description = "ok"
        declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
        input_model = _GoodInput

        async def execute(
            self, input: dict[str, Any], ctx: ToolExecutionContext
        ) -> Any:
            return None

    return _Concrete


def test_non_basemodel_input_model_rejected_at_class_creation() -> None:
    with pytest.raises(ToolDeclarationError):

        class _Bad(BaseTool):
            name = "bad"
            description = "bad"
            declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
            input_model = dict  # type: ignore[assignment]  # not a BaseModel subclass

            async def execute(
                self, input: dict[str, Any], ctx: ToolExecutionContext
            ) -> Any:
                return None


def test_inheriting_execute_does_not_bypass_validation() -> None:
    parent = _make_concrete_base()
    with pytest.raises(ToolDeclarationError):

        class _ChildMissingName(parent):  # type: ignore[valid-type, misc]
            # Inherits execute from parent; only the missing field should trip.
            name = None  # type: ignore[assignment]
            description = "child"
            declaration = ToolDeclaration(risk="safe", tier_required=0, timeout_seconds=1.0)
            input_model = _GoodInput


def test_inheriting_execute_with_bad_input_model_rejected() -> None:
    parent = _make_concrete_base()
    with pytest.raises(ToolDeclarationError):

        class _ChildBadModel(parent):  # type: ignore[valid-type, misc]
            input_model = int  # type: ignore[assignment]


def test_abstract_subclass_is_not_validated() -> None:
    from abc import abstractmethod

    # An abstract intermediate layer with partial state must NOT trip the gate.
    class _AbstractLayer(BaseTool):
        @abstractmethod
        async def execute(
            self, input: dict[str, Any], ctx: ToolExecutionContext
        ) -> Any: ...

    assert _AbstractLayer is not None


def test_well_formed_concrete_subclass_accepted() -> None:
    cls = _make_concrete_base()
    assert cls.name == "concrete"
