from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal

from guitar_helper.db.interfaces import Preset, Segment, Track

_COLUMNS = ("Start", "End", "Tone", "Confidence", "Corrected")
_TRACK_COLUMNS = ("Title", "Artist", "Date added", "Progress")
_PRESET_COLUMNS = ("Tone", "Preset name", "PC")
_PRESET_NAME_COL = 1
_PRESET_PC_COL = 2


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


class PresetTableModel(QAbstractTableModel):
    """Editable view of the presets table. Edits are forwarded as intent —
    the model never writes to the store, so validation stays in one place
    (ui/editor/preset_validation.py, called by OutputMode)."""

    pcEditRequested = Signal(str, int)    # tone_label, new pc_number
    nameEditRequested = Signal(str, str)  # tone_label, new preset_name
    editRejected = Signal(str)            # message for non-numeric PC input

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._presets: list[Preset] = []

    def set_presets(self, presets: list[Preset]) -> None:
        self.beginResetModel()
        self._presets = list(presets)
        self.endResetModel()

    def preset_at_row(self, row: int) -> Preset:
        return self._presets[row]

    @property
    def presets(self) -> list[Preset]:
        return list(self._presets)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._presets)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_PRESET_COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return _PRESET_COLUMNS[section]

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = super().flags(index)
        if index.column() in (_PRESET_NAME_COL, _PRESET_PC_COL):
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        preset = self._presets[index.row()]
        column = index.column()
        if column == 0:
            return preset.tone_label
        if column == _PRESET_NAME_COL:
            return preset.preset_name
        if column == _PRESET_PC_COL:
            if preset.pc_number < 0 and role == Qt.ItemDataRole.DisplayRole:
                return f"{preset.pc_number} (no dispatch)"
            return str(preset.pc_number)
        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        """Always returns False: the model holds no authority over the data, it
        only emits intent. OutputMode validates, writes to the store, and pushes
        the result back via set_presets — so the table never shows a value that
        was rejected, not even for a frame."""
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        tone_label = self._presets[index.row()].tone_label
        if index.column() == _PRESET_NAME_COL:
            self.nameEditRequested.emit(tone_label, str(value))
        elif index.column() == _PRESET_PC_COL:
            try:
                pc = int(str(value).strip().split()[0])
            except (ValueError, IndexError):
                self.editRejected.emit("Program Change must be a whole number.")
            else:
                self.pcEditRequested.emit(tone_label, pc)
        return False
