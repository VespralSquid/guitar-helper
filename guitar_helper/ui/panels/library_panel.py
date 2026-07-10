"""Track list built from store.list_tracks(); selection -> load intent."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from guitar_helper.db.interfaces import ISegmentStore, Track

_TRACK_ROLE = Qt.ItemDataRole.UserRole


class LibraryPanel(QWidget):
    trackChosen = Signal(object)  # Track

    def __init__(self, store: ISegmentStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list_widget)

        self.refresh()

    def refresh(self) -> None:
        self.list_widget.clear()
        for track in self._store.list_tracks():
            item = QListWidgetItem(_label(track))
            item.setData(_TRACK_ROLE, track)
            if track.source_path is None:
                item.setToolTip("No source path on record — cannot be reloaded from here.")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list_widget.addItem(item)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        track: Track = item.data(_TRACK_ROLE)
        if track.source_path is not None:
            self.trackChosen.emit(track)


def _label(track: Track) -> str:
    title = track.title or track.filename
    artist = f"{track.artist} — " if track.artist else ""
    progress = f"[{track.corrected_count}/{track.total_count}]" if track.total_count else "[no segments]"
    return f"{artist}{title} {progress}"
