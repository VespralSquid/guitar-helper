from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from guitar_helper.db.interfaces import Segment

_COLUMNS = ("Start", "End", "Tone", "Confidence", "Corrected")


def _format_ms(ms: int) -> str:
    total_s = ms / 1000
    return f"{total_s:.3f}s"


class SegmentTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._segments: list[Segment] = []

    def set_segments(self, segments: list[Segment]) -> None:
        self.beginResetModel()
        self._segments = list(segments)
        self.endResetModel()

    def segment_at_row(self, row: int) -> Segment:
        return self._segments[row]

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._segments)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return _COLUMNS[section]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        segment = self._segments[index.row()]
        column = index.column()
        if column == 0:
            return _format_ms(segment.start_ms)
        if column == 1:
            return _format_ms(segment.end_ms)
        if column == 2:
            return segment.tone_label
        if column == 3:
            return f"{segment.confidence:.2f}"
        if column == 4:
            return "yes" if segment.manually_corrected else ""
        return None
