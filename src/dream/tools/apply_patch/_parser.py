"""Parse Codex patch text into a :class:`Patch` AST."""

from __future__ import annotations

from dataclasses import dataclass, field

from dream.tools.apply_patch._tokens import (
    _ADD_BOUNDARIES,
    _HUNK_LINE_END,
    _UPDATE_SECTION_END,
    PatchMarker,
)
from dream.tools.apply_patch._types import (
    ActionType,
    Chunk,
    DiffError,
    Patch,
    PatchAction,
)


@dataclass(slots=True)
class _Parser:
    current_files: dict[str, str]
    lines: list[str]
    index: int = 0
    patch: Patch = field(default_factory=Patch)
    fuzz: int = 0

    def is_done(self, prefixes: tuple[str, ...] | None = None) -> bool:
        if self.index >= len(self.lines):
            return True
        return bool(prefixes and self.lines[self.index].startswith(prefixes))

    def startswith(self, prefix: str | tuple[str, ...]) -> bool:
        if self.index >= len(self.lines):
            raise DiffError(f"Index: {self.index} >= {len(self.lines)}")
        return self.lines[self.index].startswith(prefix)

    def read_str(self, prefix: str = "", *, return_everything: bool = False) -> str:
        if self.index >= len(self.lines):
            raise DiffError(f"Index: {self.index} >= {len(self.lines)}")
        line = self.lines[self.index]
        if line.startswith(prefix):
            text = line if return_everything else line[len(prefix) :]
            self.index += 1
            return text
        return ""

    def parse(self) -> None:
        while not self.is_done((PatchMarker.END,)):
            path = self.read_str(PatchMarker.UPDATE)
            if path:
                self._parse_update(path)
                continue
            path = self.read_str(PatchMarker.DELETE)
            if path:
                self._parse_delete(path)
                continue
            path = self.read_str(PatchMarker.ADD)
            if path:
                self._parse_add(path)
                continue
            raise DiffError(f"Unknown Line: {self.lines[self.index]}")
        if not self.startswith((PatchMarker.END,)):
            raise DiffError("Missing End Patch")
        self.index += 1
        _validate_destinations(self.patch)

    def _parse_update(self, path: str) -> None:
        if path in self.patch.actions:
            raise DiffError(f"Update File Error: Duplicate Path: {path}")
        move_to = self.read_str(PatchMarker.MOVE)
        if path not in self.current_files:
            raise DiffError(f"Update File Error: Missing File: {path}")
        action = self._parse_update_hunks(self.current_files[path])
        action.move_path = move_to or None
        if move_to and move_to in self.current_files:
            raise DiffError(f"Move File Error: Destination already exists: {move_to}")
        self.patch.actions[path] = action

    def _parse_delete(self, path: str) -> None:
        if path in self.patch.actions:
            raise DiffError(f"Delete File Error: Duplicate Path: {path}")
        if path not in self.current_files:
            raise DiffError(f"Delete File Error: Missing File: {path}")
        self.patch.actions[path] = PatchAction(type=ActionType.DELETE)

    def _parse_add(self, path: str) -> None:
        if path in self.patch.actions:
            raise DiffError(f"Add File Error: Duplicate Path: {path}")
        if path in self.current_files:
            raise DiffError(f"Add File Error: File already exists: {path}")
        self.patch.actions[path] = self._parse_add_body()

    def _parse_update_hunks(self, text: str) -> PatchAction:
        action = PatchAction(type=ActionType.UPDATE)
        lines = text.split("\n")
        cursor = 0
        while not self.is_done(_UPDATE_SECTION_END):
            anchor = self.read_str(PatchMarker.HUNK)
            bare_hunk = False
            if not anchor and self.index < len(self.lines) and self.lines[self.index] == PatchMarker.HUNK_BARE:
                bare_hunk = True
                self.index += 1
            if not (anchor or bare_hunk or cursor == 0):
                raise DiffError(f"Invalid Line:\n{self.lines[self.index]}")
            if anchor.strip():
                cursor = _seek_anchor(lines, cursor, anchor, fuzz_counter=self)
            context, chunks, end_index, eof = _peek_hunk(self.lines, self.index)
            match_index, fuzz = _find_context(lines, context, cursor, eof)
            if match_index == -1:
                label = "Invalid EOF Context" if eof else "Invalid Context"
                raise DiffError(f"{label} {cursor}:\n" + "\n".join(context))
            self.fuzz += fuzz
            for chunk in chunks:
                chunk.orig_index += match_index
                action.chunks.append(chunk)
            cursor = match_index + len(context)
            self.index = end_index
        return action

    def _parse_add_body(self) -> PatchAction:
        body: list[str] = []
        while not self.is_done(_ADD_BOUNDARIES):
            line = self.read_str()
            if not line.startswith("+"):
                raise DiffError(f"Invalid Add File Line: {line}")
            body.append(line[1:])
        return PatchAction(type=ActionType.ADD, new_file="\n".join(body))


def parse_patch_text(text: str, file_contents: dict[str, str]) -> tuple[Patch, int]:
    """Validate envelope and parse ``text`` against ``file_contents``."""
    lines = text.strip().split("\n")
    if len(lines) < 2 or lines[0] != PatchMarker.BEGIN or lines[-1] != PatchMarker.END:
        raise DiffError("Invalid patch text")
    parser = _Parser(current_files=file_contents, lines=lines, index=1)
    parser.parse()
    return parser.patch, parser.fuzz


def _validate_destinations(patch: Patch) -> None:
    destinations: list[str] = []
    for path, action in patch.actions.items():
        if action.type == ActionType.ADD:
            destinations.append(path)
        elif action.type == ActionType.UPDATE and action.move_path:
            destinations.append(action.move_path)
    if len(destinations) != len(set(destinations)):
        raise DiffError("Patch Error: Duplicate destination path")


def _seek_anchor(lines: list[str], start: int, anchor: str, *, fuzz_counter: _Parser) -> int:
    if anchor in lines[:start]:
        return start
    for index, line in enumerate(lines[start:], start):
        if line == anchor:
            return index + 1
    if anchor.strip() not in {segment.strip() for segment in lines[:start]}:
        for index, line in enumerate(lines[start:], start):
            if line.strip() == anchor.strip():
                fuzz_counter.fuzz += 1
                return index + 1
    return start


def _find_context_core(lines: list[str], context: list[str], start: int) -> tuple[int, int]:
    if not context:
        return start, 0
    for index in range(start, len(lines)):
        if lines[index : index + len(context)] == context:
            return index, 0
    for index in range(start, len(lines)):
        if [segment.rstrip() for segment in lines[index : index + len(context)]] == [
            segment.rstrip() for segment in context
        ]:
            return index, 1
    for index in range(start, len(lines)):
        if [segment.strip() for segment in lines[index : index + len(context)]] == [
            segment.strip() for segment in context
        ]:
            return index, 100
    return -1, 0


def _find_context(
    lines: list[str], context: list[str], start: int, eof: bool
) -> tuple[int, int]:
    if eof:
        match_index, fuzz = _find_context_core(lines, context, len(lines) - len(context))
        if match_index != -1:
            return match_index, fuzz
        match_index, fuzz = _find_context_core(lines, context, start)
        return match_index, fuzz + 10000
    return _find_context_core(lines, context, start)


def _peek_hunk(
    lines: list[str], index: int
) -> tuple[list[str], list[Chunk], int, bool]:
    kept: list[str] = []
    deleted: list[str] = []
    inserted: list[str] = []
    chunks: list[Chunk] = []
    mode = "keep"
    start = index
    while index < len(lines):
        raw = lines[index]
        if raw.startswith(_HUNK_LINE_END):
            break
        if raw == "***":
            break
        if raw.startswith("***"):
            raise DiffError(f"Invalid Line: {raw}")
        index += 1
        prior_mode = mode
        line = " " if raw == "" else raw
        if line[0] == "+":
            mode = "add"
        elif line[0] == "-":
            mode = "delete"
        elif line[0] == " ":
            mode = "keep"
        else:
            raise DiffError(f"Invalid Line: {line}")
        payload = line[1:]
        if mode == "keep" and prior_mode != mode and (inserted or deleted):
            chunks.append(
                Chunk(
                    orig_index=len(kept) - len(deleted),
                    del_lines=deleted,
                    ins_lines=inserted,
                )
            )
            deleted = []
            inserted = []
        if mode == "delete":
            deleted.append(payload)
            kept.append(payload)
        elif mode == "add":
            inserted.append(payload)
        else:
            kept.append(payload)
    if inserted or deleted:
        chunks.append(
            Chunk(
                orig_index=len(kept) - len(deleted),
                del_lines=deleted,
                ins_lines=inserted,
            )
        )
    if index < len(lines) and lines[index] == PatchMarker.EOF:
        return kept, chunks, index + 1, True
    if index == start:
        raise DiffError(f"Nothing in this section - index={index} {lines[index]}")
    return kept, chunks, index, False


__all__ = ["parse_patch_text"]
