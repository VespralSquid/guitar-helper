"""Qt adapter over EditorState: translates StateEvent -> Qt signals so views
can `connect()` without EditorState itself importing Qt."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from guitar_helper.ui.state.editor_state import EditorState, StateEvent


class EditorStateBridge(QObject):
    loaded = Signal()
    selectionChanged = Signal(object)  # int | None
    segmentsChanged = Signal()
    dirtyChanged = Signal(bool)
    savedChanged = Signal()

    def __init__(self, state: EditorState) -> None:
        super().__init__()
        self._state = state
        state.subscribe(self._on_event)

    def _on_event(self, event: StateEvent) -> None:
        if event.kind == "loaded":
            self.loaded.emit()
        elif event.kind == "selection":
            self.selectionChanged.emit(self._state.selection_index)
        elif event.kind == "segments":
            self.segmentsChanged.emit()
        elif event.kind == "dirty":
            self.dirtyChanged.emit(self._state.dirty)
        elif event.kind == "saved":
            self.savedChanged.emit()
