from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from guitar_helper.db.interfaces import Segment, Track

_COLUMNS = ("Start", "End", "Tone", "Confidence", "Corrected")
_TRACK_COLUMNS = ("Title", "Artist", "Date added", "Progress")


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


class TrackTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tracks: list[Track] = []

    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self._tracks = list(tracks)
        self.endResetModel()

    def track_at_row(self, row: int) -> Track:
        return self._tracks[row]

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_TRACK_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return _TRACK_COLUMNS[section]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        track = self._tracks[index.row()]
        column = index.column()
        if column == 0:
            return track.title or track.filename
        if column == 1:
            return track.artist or ""
        if column == 2:
            return track.analysed_at[:10]  # ISO date part
        if column == 3:
            return f"{track.corrected_count}/{track.total_count}" if track.total_count else "no segments"
        return None
