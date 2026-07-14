"""Home mode: playlist-first library (AC1/2/4/5).

Left: playlists — a virtual "Library" playlist (all songs, not a DB row) plus
the user's playlists. Right: the selected playlist's songs (Title, Artist,
Date added, progress). Double-click plays; the shell seeds the queue from the
playlist. Playlist CRUD and membership go straight to the store (main-thread
reads/writes, per the threading invariant)."""
from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from guitar_helper.db.interfaces import Track
from guitar_helper.ui.models.qt_adapters import TrackTableModel

_PLAYLIST_ID_ROLE = Qt.ItemDataRole.UserRole  # int | None (None = virtual Library)
_NEW_PLAYLIST = "New playlist"


class HomeMode(QWidget):
    playRequested = Signal(object, int)  # (list[Track] queue, start index)

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store

        self.stats_label = QLabel()
        self.hint_label = QLabel(
            "No playlists yet — create your first playlist to organise your library."
        )
        self.hint_label.setWordWrap(True)

        self.playlist_list = QListWidget()
        self.playlist_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.new_playlist_button = QPushButton(_NEW_PLAYLIST)

        self.track_model = TrackTableModel()
        self.song_table = QTableView()
        self.song_table.setModel(self.track_model)
        self.song_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.song_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.song_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.song_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.song_table.horizontalHeader().setStretchLastSection(True)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Playlists"))
        left_layout.addWidget(self.playlist_list, stretch=1)
        left_layout.addWidget(self.new_playlist_button)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.hint_label)
        right_layout.addWidget(self.song_table, stretch=1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        layout = QVBoxLayout(self)
        layout.addWidget(self.stats_label)
        layout.addWidget(splitter, stretch=1)

        self.playlist_list.currentItemChanged.connect(self._on_playlist_selected)
        self.playlist_list.customContextMenuRequested.connect(self._on_playlist_menu)
        self.new_playlist_button.clicked.connect(self._on_new_playlist)
        self.song_table.doubleClicked.connect(self._on_song_double_clicked)
        self.song_table.customContextMenuRequested.connect(self._on_song_menu)

        self.refresh()

    # ------------------------------------------------------------------
    # refresh / data
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        selected_id = self.current_playlist_id()
        self.playlist_list.blockSignals(True)
        self.playlist_list.clear()

        library_item = QListWidgetItem("Library (all songs)")
        library_item.setData(_PLAYLIST_ID_ROLE, None)
        self.playlist_list.addItem(library_item)

        playlists = self._store.list_playlists()
        restore_row = 0
        for playlist in playlists:
            item = QListWidgetItem(f"{playlist.name} ({playlist.track_count})")
            item.setData(_PLAYLIST_ID_ROLE, playlist.id)
            self.playlist_list.addItem(item)
            if playlist.id == selected_id:
                restore_row = self.playlist_list.count() - 1
        self.playlist_list.blockSignals(False)

        self.hint_label.setVisible(not playlists)
        self.playlist_list.setCurrentRow(restore_row)
        self._reload_songs()
        self._refresh_stats()

    def current_playlist_id(self) -> int | None:
        item = self.playlist_list.currentItem()
        return item.data(_PLAYLIST_ID_ROLE) if item else None

    def _reload_songs(self) -> None:
        playlist_id = self.current_playlist_id()
        if playlist_id is None:
            tracks = self._store.list_tracks()
        else:
            tracks = self._store.get_playlist_tracks(playlist_id)
        self.track_model.set_tracks(tracks)

    def _refresh_stats(self) -> None:
        tracks = self._store.list_tracks()
        total_segments = sum(t.total_count for t in tracks)
        fully = sum(1 for t in tracks if t.total_count and t.corrected_count >= t.total_count)
        self.stats_label.setText(
            f"{len(tracks)} tracks · {total_segments} segments · {fully} fully corrected"
        )

    # ------------------------------------------------------------------
    # slots
    # ------------------------------------------------------------------

    def _on_playlist_selected(self, *_args) -> None:
        self._reload_songs()

    def _on_song_double_clicked(self, index: QModelIndex) -> None:
        tracks = self.track_model.tracks
        track = tracks[index.row()]
        if track.source_path:
            self.playRequested.emit(tracks, index.row())

    def _on_new_playlist(self) -> None:
        name, ok = QInputDialog.getText(self, _NEW_PLAYLIST, "Playlist name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            self._store.create_playlist(name)
        except Exception:
            QMessageBox.warning(self, _NEW_PLAYLIST, f"A playlist named {name!r} already exists.")
            return
        self.refresh()

    def _on_playlist_menu(self, pos: QPoint) -> None:
        item = self.playlist_list.itemAt(pos)
        if item is None or item.data(_PLAYLIST_ID_ROLE) is None:
            return  # no context actions on the virtual Library
        menu = QMenu(self)
        delete_action = menu.addAction("Delete playlist")
        if menu.exec(self.playlist_list.mapToGlobal(pos)) is delete_action:
            self._store.delete_playlist(item.data(_PLAYLIST_ID_ROLE))
            self.refresh()

    def _on_song_menu(self, pos: QPoint) -> None:
        index = self.song_table.indexAt(pos)
        if not index.isValid():
            return
        track: Track = self.track_model.track_at_row(index.row())
        menu = QMenu(self)

        add_menu = menu.addMenu("Add to playlist")
        playlists = self._store.list_playlists()
        add_actions = {add_menu.addAction(p.name): p.id for p in playlists}
        if not playlists:
            add_menu.setEnabled(False)

        remove_action = None
        playlist_id = self.current_playlist_id()
        if playlist_id is not None:
            remove_action = menu.addAction("Remove from this playlist")

        chosen = menu.exec(self.song_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen in add_actions:
            self._store.add_to_playlist(add_actions[chosen], track.file_hash)
            self.refresh()
        elif chosen is remove_action:
            self._store.remove_from_playlist(playlist_id, track.file_hash)
            self.refresh()
