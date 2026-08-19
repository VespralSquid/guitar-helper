"""Home mode: playlist-first library (AC1/2/4/5).

Left: playlists — a virtual "Library" playlist (all songs, not a DB row) plus
the user's playlists. Right: the selected playlist's songs (Title, Artist,
Date added, progress). Double-click plays; the shell seeds the queue from the
playlist. Playlist CRUD and membership go straight to the store (main-thread
reads/writes, per the threading invariant)."""
from __future__ import annotations

import sqlite3

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
_PLAYLIST_NAME_ROLE = Qt.ItemDataRole.UserRole + 1  # clean name, no " (count)" suffix
_NEW_PLAYLIST = "New playlist"
_LIBRARY_NAME = "Library"
_ADD_SONGS = "Add songs…"
_REMOVE_FROM_LIBRARY = "Remove from library"


class HomeMode(QWidget):
    playRequested = Signal(object, int)  # (list[Track] queue, start index)
    # Opens already-analysed tracks in the segment editor. Named for what it
    # does: the button used to say "Analyze", which is what Add songs does.
    correctLabelsRequested = Signal(str, object, int)  # playlist_name, list[Track], start_index
    addSongsRequested = Signal(object)  # playlist_id (int) or None for the virtual Library
    trackRemoved = Signal(str)  # file_hash, after the row is gone from the DB

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store

        self.stats_label = QLabel()
        self.hint_label = QLabel(
            "No playlists yet — create your first playlist to organise your library."
        )
        self.hint_label.setWordWrap(True)
        self.empty_library_label = QLabel(
            "Your library is empty. Click “Add songs…” to analyse your first track."
        )
        self.empty_library_label.setWordWrap(True)

        self.playlist_list = QListWidget()
        self.playlist_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.new_playlist_button = QPushButton(_NEW_PLAYLIST)
        self.add_songs_button = QPushButton(_ADD_SONGS)
        self.correct_labels_button = QPushButton("Correct labels")

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
        left_layout.addWidget(self.add_songs_button)
        left_layout.addWidget(self.correct_labels_button)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.hint_label)
        right_layout.addWidget(self.empty_library_label)
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
        self.add_songs_button.clicked.connect(self._on_add_songs_clicked)
        self.song_table.doubleClicked.connect(self._on_song_double_clicked)
        self.song_table.customContextMenuRequested.connect(self._on_song_menu)
        self.correct_labels_button.clicked.connect(self._on_correct_labels_clicked)

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
        library_item.setData(_PLAYLIST_NAME_ROLE, _LIBRARY_NAME)
        self.playlist_list.addItem(library_item)

        playlists = self._store.list_playlists()
        restore_row = 0
        for playlist in playlists:
            item = QListWidgetItem(f"{playlist.name} ({playlist.track_count})")
            item.setData(_PLAYLIST_ID_ROLE, playlist.id)
            item.setData(_PLAYLIST_NAME_ROLE, playlist.name)
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

    def current_playlist_name(self) -> str:
        item = self.playlist_list.currentItem()
        return item.data(_PLAYLIST_NAME_ROLE) if item else _LIBRARY_NAME

    def _reload_songs(self) -> None:
        playlist_id = self.current_playlist_id()
        if playlist_id is None:
            tracks = self._store.list_tracks()
        else:
            tracks = self._store.get_playlist_tracks(playlist_id)
        self.track_model.set_tracks(tracks)
        self.correct_labels_button.setEnabled(bool(tracks))

    def _refresh_stats(self) -> None:
        tracks = self._store.list_tracks()
        total_segments = sum(t.total_count for t in tracks)
        fully = sum(1 for t in tracks if t.total_count and t.corrected_count >= t.total_count)
        self.stats_label.setText(
            f"{len(tracks)} tracks · {total_segments} segments · {fully} fully corrected"
        )
        self.empty_library_label.setVisible(not tracks)

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

    def _on_correct_labels_clicked(self) -> None:
        tracks = self.track_model.tracks
        if not tracks:
            return
        rows = self.song_table.selectionModel().selectedRows()
        start_index = rows[0].row() if rows else 0
        self.correctLabelsRequested.emit(self.current_playlist_name(), tracks, start_index)

    def _on_add_songs_clicked(self) -> None:
        self.addSongsRequested.emit(self.current_playlist_id())

    def _on_new_playlist(self) -> None:
        name, ok = QInputDialog.getText(self, _NEW_PLAYLIST, "Playlist name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            self._store.create_playlist(name)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, _NEW_PLAYLIST, f"A playlist named {name!r} already exists.")
            return
        self.refresh()

    def _on_playlist_menu(self, pos: QPoint) -> None:
        item = self.playlist_list.itemAt(pos)
        if item is None:
            return
        playlist_id = item.data(_PLAYLIST_ID_ROLE)
        menu = QMenu(self)
        add_action = menu.addAction("Add songs to this playlist")
        # The virtual Library is not a row anyone can delete.
        delete_action = menu.addAction("Delete playlist") if playlist_id is not None else None

        chosen = menu.exec(self.playlist_list.mapToGlobal(pos))
        if chosen is add_action:
            self.addSongsRequested.emit(playlist_id)
        elif delete_action is not None and chosen is delete_action:
            self._store.delete_playlist(playlist_id)
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
        menu.addSeparator()
        delete_action = menu.addAction(_REMOVE_FROM_LIBRARY)

        chosen = menu.exec(self.song_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen in add_actions:
            self._store.add_to_playlist(add_actions[chosen], track.file_hash)
            self.refresh()
        elif chosen is remove_action:
            self._store.remove_from_playlist(playlist_id, track.file_hash)
            self.refresh()
        elif chosen is delete_action:
            self.remove_from_library(track)

    # ------------------------------------------------------------------
    # library removal
    # ------------------------------------------------------------------

    def remove_from_library(self, track: Track) -> bool:
        """Confirm, then delete the track and everything referencing it.

        The confirmation names the corrected-segment count because those are
        calibration ground truth and the project has already lost a set once —
        a generic "Are you sure?" does not put that cost in front of the
        decision."""
        if not self._confirm_removal(track):
            return False
        self._store.delete_track(track.file_hash)
        self.refresh()
        self.trackRemoved.emit(track.file_hash)
        return True

    def _confirm_removal(self, track: Track) -> bool:
        title = track.title or track.filename
        if track.corrected_count:
            plural = "" if track.corrected_count == 1 else "s"
            cost = (
                f"This will permanently delete {track.corrected_count} corrected "
                f"segment{plural}, which cannot be recovered."
            )
        else:
            cost = f"This will delete its {track.total_count} analysed segments."
        choice = QMessageBox.warning(
            self,
            _REMOVE_FROM_LIBRARY,
            f"Remove “{title}” from your library?\n\n{cost}\n\n"
            "The audio file itself is not touched, and the separated guitar stem "
            "is kept — re-adding this file will not repeat separation.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return choice == QMessageBox.StandardButton.Yes
