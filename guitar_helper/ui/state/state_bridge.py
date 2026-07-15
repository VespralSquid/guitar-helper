"""Qt adapters over the Qt-free state objects: translate their plain-callback
events into Qt signals so views can `connect()` without the state classes
importing Qt."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from guitar_helper.ui.state.editor_state import EditorState, StateEvent
from guitar_helper.ui.state.queue_state import QueueEvent, QueueState


class EditorStateBridge(QObject):
    loaded = Signal()
    selectionChanged = Signal(object)  # int | None
    segmentsChanged = Signal()
    dirtyChanged = Signal(bool)
    savedChanged = Signal()
    excludedChanged = Signal(bool)

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
        elif event.kind == "excluded":
            self.excludedChanged.emit(self._state.excluded)


class QueueStateBridge(QObject):
    queueChanged = Signal()
    currentChanged = Signal(object)  # int | None

    def __init__(self, state: QueueState) -> None:
        super().__init__()
        self._state = state
        state.subscribe(self._on_event)

    def _on_event(self, event: QueueEvent) -> None:
        if event.kind == "queue":
            self.queueChanged.emit()
        elif event.kind == "current":
            self.currentChanged.emit(self._state.current_index)
