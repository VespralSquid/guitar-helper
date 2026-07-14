"""Analysis mode: segment-colored timeline + segment table (AC8: segments
over waveform — the waveform is gone entirely, replaced by SegmentTimeline).
Owns its widgets and internal wiring; talks to the shell only through
signals and plain methods — no EditorState or Application access here."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from guitar_helper.db.interfaces import Segment
from guitar_helper.ui.models.qt_adapters import SegmentTableModel
from guitar_helper.ui.views.segment_timeline import SegmentTimeline


class AnalysisMode(QWidget):
    seekRequested = Signal(int)   # timeline click, position_ms
    rowSelected = Signal(object)  # int | None, from table selection

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.timeline = SegmentTimeline()

        self.segment_model = SegmentTableModel()
        self.segment_table = QTableView()
        self.segment_table.setModel(self.segment_model)
        self.segment_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.segment_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.segment_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        layout = QVBoxLayout(self)
        layout.addWidget(self.timeline)
        layout.addWidget(self.segment_table, stretch=1)

        self.timeline.seekRequested.connect(self.seekRequested)
        self.segment_table.selectionModel().selectionChanged.connect(self._on_table_selection)

    def set_duration_ms(self, duration_ms: int) -> None:
        self.timeline.set_duration_ms(duration_ms)

    def set_segments(self, segments: list[Segment]) -> None:
        self.segment_model.set_segments(segments)
        self.timeline.set_segments(segments)

    def set_selected(self, index: int | None) -> None:
        self.timeline.set_selected(index)
        if index is None:
            self.segment_table.clearSelection()
        else:
            self.segment_table.selectRow(index)

    def set_playhead_ms(self, position_ms: int) -> None:
        self.timeline.set_playhead_ms(position_ms)

    def _on_table_selection(self, *_args) -> None:
        rows = self.segment_table.selectionModel().selectedRows()
        self.rowSelected.emit(rows[0].row() if rows else None)
