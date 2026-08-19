"""Qt-free single source of truth for the segment editor.

Import guard: no `PySide6`/`pyqtgraph` import here — this class is imported
and unit-tested without a QApplication. `EditorStateBridge` (state_bridge.py)
adapts it to Qt signals for views.
"""
from __future__ import annotations

import bisect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from guitar_helper.db.interfaces import ISegmentStore, Segment
from guitar_helper.ui.editor.merge import find_same_tone_run
from guitar_helper.ui.editor.merge import merge_run as _build_merge_plan
from guitar_helper.ui.editor.validation import (
    EditResult,
    apply_boundary,
    apply_boundary_move,
    apply_confirm,
    apply_relabel,
    validate_boundary,
    validate_boundary_move,
    validate_relabel,
)

__all__ = ["EditResult", "EditorState", "StateEvent"]

_INDEX_OUT_OF_RANGE = EditResult(False, "Index out of range.")


@dataclass
class StateEvent:
    kind: Literal["loaded", "selection", "segments", "dirty", "saved", "excluded"]


class EditorState:
    """Loaded track's segments, selection, and pending edits.

    Edits (relabel, boundary, confirm, merge) validate against the working
    list, mutate it in place for live UI feedback, and queue the DB write in
    `_pending`/`_deleted_ids`. Nothing touches the store until `save()`.
    """

    def __init__(self, store: ISegmentStore, tone_labels: tuple[str, ...]) -> None:
        self._store = store
        self._tone_labels = tone_labels
        self._file_hash: str | None = None
        self._segments: list[Segment] = []
        self._selection_index: int | None = None
        self._pending: dict[int, Segment] = {}
        self._deleted_ids: set[int] = set()
        self._excluded: bool = False
        self._subscribers: list[Callable[[StateEvent], None]] = []

    # ------------------------------------------------------------------
    # load / lifecycle
    # ------------------------------------------------------------------

    def load_track(self, file_hash: str) -> None:
        self._file_hash = file_hash
        self._segments = self._store.get_segments(file_hash)
        self._selection_index = None
        self._excluded = self._store.get_calibration_excluded(file_hash)
        self._pending.clear()
        self._deleted_ids.clear()
        self._emit(StateEvent("loaded"))

    def clear(self) -> None:
        self._file_hash = None
        self._segments = []
        self._selection_index = None
        self._excluded = False
        self._pending.clear()
        self._deleted_ids.clear()
        self._emit(StateEvent("loaded"))

    # ------------------------------------------------------------------
    # read accessors
    # ------------------------------------------------------------------

    @property
    def file_hash(self) -> str | None:
        return self._file_hash

    @property
    def segments(self) -> list[Segment]:
        return list(self._segments)

    @property
    def selection_index(self) -> int | None:
        return self._selection_index

    @property
    def dirty(self) -> bool:
        return bool(self._pending or self._deleted_ids)

    @property
    def excluded(self) -> bool:
        return self._excluded

    def calibration_copy_exists(self) -> bool:
        if self._file_hash is None:
            return False
        return bool(self._store.get_calibration_segments(self._file_hash))

    def segment_at(self, position_ms: int) -> int | None:
        """Index of the segment covering position_ms, or None."""
        starts = [s.start_ms for s in self._segments]
        i = bisect.bisect_right(starts, position_ms) - 1
        if 0 <= i < len(self._segments) and self._segments[i].end_ms > position_ms:
            return i
        return None

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------

    def select(self, index: int | None) -> None:
        if index is not None and not (0 <= index < len(self._segments)):
            return
        if index == self._selection_index:
            return
        self._selection_index = index
        self._emit(StateEvent("selection"))

    # ------------------------------------------------------------------
    # edit ops (validate -> mutate working list -> queue pending -> emit)
    # ------------------------------------------------------------------

    def relabel(self, index: int, label: str) -> EditResult:
        if not (0 <= index < len(self._segments)):
            return _INDEX_OUT_OF_RANGE
        result = validate_relabel(label, self._tone_labels)
        if not result.ok:
            return result
        self._apply_edit(index, apply_relabel(self._segments[index], label))
        return result

    def edit_boundary(self, index: int, start_ms: int, end_ms: int) -> EditResult:
        if not (0 <= index < len(self._segments)):
            return _INDEX_OUT_OF_RANGE
        result = validate_boundary(self._segments, index, start_ms, end_ms)
        if not result.ok:
            return result
        seg = self._segments[index]
        self._apply_edit(index, apply_boundary(seg, start_ms, end_ms, seg.tone_label))
        return result

    def move_boundary(self, left_index: int, new_ms: int) -> EditResult:
        if not (0 <= left_index < len(self._segments) - 1):
            return _INDEX_OUT_OF_RANGE
        result = validate_boundary_move(self._segments, left_index, new_ms)
        if not result.ok:
            return result
        left, right = apply_boundary_move(
            self._segments[left_index], self._segments[left_index + 1], new_ms
        )
        self._segments[left_index] = left
        self._segments[left_index + 1] = right
        self._pending[left.id] = left
        self._pending[right.id] = right
        self._emit(StateEvent("segments"))
        self._emit(StateEvent("dirty"))
        return result

    def confirm(self, index: int) -> EditResult:
        if not (0 <= index < len(self._segments)):
            return _INDEX_OUT_OF_RANGE
        self._apply_edit(index, apply_confirm(self._segments[index]))
        return EditResult(True)

    def confirm_all(self) -> EditResult:
        for i, seg in enumerate(self._segments):
            updated = apply_confirm(seg)
            self._segments[i] = updated
            self._pending[updated.id] = updated
        self._emit(StateEvent("segments"))
        self._emit(StateEvent("dirty"))
        return EditResult(True)

    def merge_run(self, index: int) -> EditResult:
        if not (0 <= index < len(self._segments)):
            return _INDEX_OUT_OF_RANGE
        lo, hi = find_same_tone_run(self._segments, index)
        if lo == hi:
            return EditResult(False, "Nothing to merge — no adjacent same-tone segment.")
        plan = _build_merge_plan(self._segments, lo, hi)
        for seg_id in plan.delete_ids:
            self._pending.pop(seg_id, None)
            self._deleted_ids.add(seg_id)
        self._segments[lo:hi + 1] = [plan.keep]
        self._pending[plan.keep.id] = plan.keep
        self._selection_index = lo
        self._emit(StateEvent("segments"))
        self._emit(StateEvent("dirty"))
        self._emit(StateEvent("selection"))
        return EditResult(True)

    def _apply_edit(self, index: int, updated: Segment) -> None:
        self._segments[index] = updated
        self._pending[updated.id] = updated
        self._emit(StateEvent("segments"))
        self._emit(StateEvent("dirty"))

    # ------------------------------------------------------------------
    # calibration exclusion (eager write, not batched with segment edits)
    # ------------------------------------------------------------------

    def set_excluded(self, excluded: bool) -> None:
        self._store.set_calibration_excluded(self._file_hash, excluded)
        self._excluded = excluded
        self._emit(StateEvent("excluded"))

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        if not self.dirty:
            return
        self._store.apply_edits(
            self._file_hash, list(self._pending.values()), list(self._deleted_ids)
        )
        self._pending.clear()
        self._deleted_ids.clear()
        self._emit(StateEvent("dirty"))
        self._emit(StateEvent("saved"))

    def discard(self) -> None:
        self._segments = self._store.get_segments(self._file_hash)
        self._pending.clear()
        self._deleted_ids.clear()
        self._emit(StateEvent("segments"))
        self._emit(StateEvent("dirty"))

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[StateEvent], None]) -> None:
        self._subscribers.append(callback)

    def _emit(self, event: StateEvent) -> None:
        for callback in self._subscribers:
            callback(event)
