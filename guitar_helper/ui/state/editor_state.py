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


@dataclass
class EditResult:
    ok: bool
    error: str | None = None


@dataclass
class StateEvent:
    kind: Literal["loaded", "selection", "segments", "dirty", "saved", "excluded"]


class EditorState:
    """Loaded track's segments plus the current selection.

    Edit operations (relabel, boundary edit, confirm, merge, save/discard) are
    added in M4/M5; this milestone only covers load and selection.
    """

    def __init__(self, store: ISegmentStore, tone_labels: tuple[str, ...]) -> None:
        self._store = store
        self._tone_labels = tone_labels
        self._file_hash: str | None = None
        self._segments: list[Segment] = []
        self._selection_index: int | None = None
        self._subscribers: list[Callable[[StateEvent], None]] = []

    # ------------------------------------------------------------------
    # load / lifecycle
    # ------------------------------------------------------------------

    def load_track(self, file_hash: str) -> None:
        self._file_hash = file_hash
        self._segments = self._store.get_segments(file_hash)
        self._selection_index = None
        self._emit(StateEvent("loaded"))

    def clear(self) -> None:
        self._file_hash = None
        self._segments = []
        self._selection_index = None
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
    # observation
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[StateEvent], None]) -> None:
        self._subscribers.append(callback)

    def _emit(self, event: StateEvent) -> None:
        for callback in self._subscribers:
            callback(event)
