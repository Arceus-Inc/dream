"""Shared task_id / sprint_number validation for the sprint package.

Re-exports from :mod:`dream.utils.identifiers` so existing
``from ._checks import checked_task_id, checked_sprint_number`` imports
continue to work without touching every consumer.
"""

from dream.utils.identifiers import checked_sprint_number, checked_task_id

__all__ = ["checked_sprint_number", "checked_task_id"]
