"""Shell QMainWindow: mode sidebar + stacked modes + persistent transport.

Owns the Application, the Qt-free EditorState (via its bridge), the load
worker, and the position timer. Each mode owns its widgets and internal
wiring (SRP rule from phase4-overhaul-plan.md §2); the shell only connects
mode signals to the controller/state."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from guitar_helper.application import Application, NoSegmentsError
from guitar_helper.db.interfaces import Track
from guitar_helper.ui import theme
from guitar_helper.ui.controllers import PlaybackController
from guitar_helper.ui.editor.validation import EditResult
from guitar_helper.ui.load_worker import LoadWorker
from guitar_helper.ui.modes.analysis import AnalysisMode
from guitar_helper.ui.modes.home import HomeMode
from guitar_helper.ui.modes.placeholders import OutputMode
from guitar_helper.ui.panels.queue_sidebar import QueueSidebar
from guitar_helper.ui.state.editor_state import EditorState
from guitar_helper.ui.state.queue_state import QueueState
from guitar_helper.ui.state.state_bridge import EditorStateBridge, QueueStateBridge
from guitar_helper.ui.transport import TransportControls

_POS_TIMER_MS = 50
_TRACK_END_EPSILON_MS = 50
_MODE_NAMES = ("Home", "Analysis", "Output")
MODE_HOME, MODE_ANALYSIS, MODE_OUTPUT = range(3)


class MainWindow(QMainWindow):
    loadFinished = Signal(str)  # file_hash; emitted after a track is fully attached

    def __init__(self, application: Application, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Guitar Helper")
        self._app = application
        self._controller = PlaybackController(application)

        tone_labels = tuple(p.tone_label for p in application.store.get_presets())
        self._state = EditorState(application.store, tone_labels)
        self._bridge = EditorStateBridge(self._state)

        self._queue = QueueState()
        self._queue_bridge = QueueStateBridge(self._queue)

        self.home = HomeMode(application.store)
        self.analysis = AnalysisMode(tone_labels)
        self.output = OutputMode()
        self.transport = TransportControls()
        self.queue_sidebar = QueueSidebar(self._queue, self._queue_bridge)
        self.queue_sidebar.setFixedWidth(theme.QUEUE_SIDEBAR_WIDTH)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("modeSidebar")
        self.sidebar.setFixedWidth(theme.SIDEBAR_WIDTH)
        self.sidebar.addItems(_MODE_NAMES)

        self._stack = QStackedWidget()
        for mode in (self.home, self.analysis, self.output):
            self._stack.addWidget(mode)

        central = QWidget()
        outer = QVBoxLayout(central)
        row = QHBoxLayout()
        row.addWidget(self.sidebar)
        row.addWidget(self._stack, stretch=1)
        row.addWidget(self.queue_sidebar)
        outer.addLayout(row, stretch=1)
        outer.addWidget(self.transport)
        self.setCentralWidget(central)

        self._load_worker: LoadWorker | None = None
        self._pending_queue: tuple[list[Track], int] | None = None
        self._pending_playlist_context: tuple[str, list[Track], int] | None = None
        self._playlist_name: str | None = None
        self._playlist_tracks: list[Track] = []
        self._playlist_index: int = 0
        self._was_playing = False
        self._last_pos_ms: int | None = None
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(_POS_TIMER_MS)
        self._pos_timer.timeout.connect(self._on_pos_tick)

        self._build_menu()
        self._wire_signals()
        self.sidebar.setCurrentRow(MODE_HOME)

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = file_menu.addAction("&Open…")
        open_action.triggered.connect(self._on_file_open)

    def _wire_signals(self) -> None:
        self.sidebar.currentRowChanged.connect(self._stack.setCurrentIndex)
        self.sidebar.currentRowChanged.connect(self._on_mode_changed)

        self.transport.playClicked.connect(self._controller.play)
        self.transport.pauseClicked.connect(self._controller.pause)
        self.transport.stopClicked.connect(self._controller.stop)
        self.transport.nextClicked.connect(self._on_next_clicked)
        self.transport.previousClicked.connect(self._on_previous_clicked)
        self.transport.seekRequested.connect(self._controller.seek)
        self.transport.loopToggled.connect(self._on_loop_toggled)

        self.home.playRequested.connect(self._on_play_requested)
        self.home.analyzeRequested.connect(self._on_analyze_requested)
        self.queue_sidebar.playAtRequested.connect(self._on_queue_play_at)
        self.analysis.seekRequested.connect(self._on_timeline_clicked)
        self.analysis.rowSelected.connect(self._state.select)
        self.analysis.analyzePrevRequested.connect(self._on_analysis_prev)
        self.analysis.analyzeNextRequested.connect(self._on_analysis_next)

        self.analysis.relabelRequested.connect(
            lambda i, label: self._show_edit_result(self._state.relabel(i, label))
        )
        self.analysis.confirmRequested.connect(
            lambda i: self._show_edit_result(self._state.confirm(i))
        )
        self.analysis.confirmAllRequested.connect(
            lambda: self._show_edit_result(self._state.confirm_all())
        )
        self.analysis.mergeRequested.connect(
            lambda i: self._show_edit_result(self._state.merge_run(i))
        )
        self.analysis.boundaryEditRequested.connect(
            lambda left, ms: self._show_edit_result(self._state.move_boundary(left, ms))
        )
        self.analysis.excludeToggled.connect(self._state.set_excluded)
        self.analysis.saveRequested.connect(self._state.save)
        self.analysis.discardRequested.connect(self._state.discard)

        self._bridge.loaded.connect(self._on_state_loaded)
        self._bridge.selectionChanged.connect(self._on_selection_changed)
        self._bridge.segmentsChanged.connect(self._on_segments_changed)
        self._bridge.dirtyChanged.connect(self.analysis.set_dirty)
        self._bridge.excludedChanged.connect(self.analysis.set_excluded)
        self._bridge.savedChanged.connect(lambda: self.statusBar().showMessage("Saved.", 2000))

    # ------------------------------------------------------------------
    # loading (async: decode on LoadWorker, attach on main thread)
    # ------------------------------------------------------------------

    def _on_file_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open audio file", "", "Audio files (*.wav *.flac *.ogg *.mp3 *.m4a);;All files (*)"
        )
        if path:
            self._load_path(path)

    def _on_play_requested(self, tracks: list[Track], index: int) -> None:
        """Home double-click: play the track, queue the rest of its playlist."""
        track = tracks[index]
        if track.source_path is None or self._load_worker is not None:
            return
        self._pending_queue = (tracks, index)
        self._load_path(track.source_path)

    def _on_analyze_requested(self, playlist_name: str, tracks: list[Track], start_index: int) -> None:
        self._enter_playlist_analysis(playlist_name, tracks, start_index)

    def _on_analysis_prev(self) -> None:
        if self._playlist_tracks:
            self._enter_playlist_analysis(
                self._playlist_name, self._playlist_tracks, self._playlist_index - 1
            )

    def _on_analysis_next(self) -> None:
        if self._playlist_tracks:
            self._enter_playlist_analysis(
                self._playlist_name, self._playlist_tracks, self._playlist_index + 1
            )

    def _enter_playlist_analysis(self, playlist_name: str, tracks: list[Track], index: int) -> None:
        """Choke point for Analyze / playlist Prev / playlist Next: bounds-checks,
        guards against navigating away from an unsaved edit, then loads via the
        normal async pipeline. Independent of the casual-listening queue."""
        if not (0 <= index < len(tracks)):
            return
        track = tracks[index]
        if track.source_path is None or self._load_worker is not None:
            return
        if not self._confirm_discard_if_dirty():
            return
        self._pending_playlist_context = (playlist_name, tracks, index)
        self.sidebar.setCurrentRow(MODE_ANALYSIS)
        self._load_path(track.source_path)

    def _confirm_discard_if_dirty(self) -> bool:
        if not self._state.dirty:
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            "Discard unsaved segment edits for this track?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        return choice == QMessageBox.StandardButton.Discard

    def _on_queue_play_at(self, index: int) -> None:
        self._play_queue_track(self._queue.play_at(index))

    def _on_next_clicked(self) -> None:
        self._play_queue_track(self._queue.advance(manual=True))

    def _on_previous_clicked(self) -> None:
        self._play_queue_track(self._queue.previous())

    def _play_queue_track(self, track: Track | None) -> None:
        if track is None:
            self._controller.stop()
            return
        if track.file_hash == self._state.file_hash:
            # Same file already decoded — restart cleanly instead of reloading.
            self._controller.stop()
            self._controller.play()
            return
        if track.source_path:
            self._load_path(track.source_path)

    def _load_path(self, path: str) -> None:
        if self._load_worker is not None:
            return
        self._set_loading(True)
        worker = LoadWorker(self._app, path, parent=self)
        worker.decoded.connect(self._on_load_decoded)
        worker.failed.connect(self._on_load_failed)
        worker.finished.connect(worker.deleteLater)
        self._load_worker = worker
        worker.start()

    def _on_load_decoded(self, file_hash: str, buffer: object, name: str) -> None:
        self._load_worker = None
        self._set_loading(False)
        try:
            self._app.attach(file_hash, buffer, name=name)
        except NoSegmentsError as exc:
            self._pending_queue = None
            self._pending_playlist_context = None
            QMessageBox.warning(self, "No segments", str(exc))
            return

        self._sync_queue_after_load(file_hash)
        self._commit_playlist_context()
        self.transport.loop_checkbox.setChecked(False)
        self._last_pos_ms = None
        self._was_playing = False
        self._state.load_track(file_hash)
        self.analysis.set_duration_ms(self._app.buffer.duration_ms)
        self.transport.set_duration_ms(self._app.buffer.duration_ms)
        self.transport.set_enabled_playback(True)
        self._pos_timer.start()
        self._controller.play()
        self._update_playlist_header()
        self.loadFinished.emit(file_hash)

    def _sync_queue_after_load(self, file_hash: str) -> None:
        """Make the queue reflect what just loaded: a Home play request seeds
        it with the playlist; loads from the queue itself already point at the
        right entry; File→Open falls back to a single-track queue."""
        if self._pending_queue is not None:
            tracks, index = self._pending_queue
            self._pending_queue = None
            self._queue.set_queue(tracks, index)
            return
        current = self._queue.current_track
        if current is not None and current.file_hash == file_hash:
            return
        track = next(
            (t for t in self._app.store.list_tracks() if t.file_hash == file_hash), None
        )
        self._queue.set_queue([track] if track else [], 0)

    def _commit_playlist_context(self) -> None:
        """Mirrors _sync_queue_after_load's pending/consume shape: a playlist
        Analyze/Prev/Next load commits its snapshot; any other load path
        (File->Open, Home double-click, queue Next/Prev) has no context."""
        if self._pending_playlist_context is not None:
            self._playlist_name, self._playlist_tracks, self._playlist_index = (
                self._pending_playlist_context
            )
            self._pending_playlist_context = None
            return
        self._playlist_name = None
        self._playlist_tracks = []
        self._playlist_index = 0

    def _update_playlist_header(self) -> None:
        if not self._playlist_tracks:
            self.analysis.set_playlist_context("", False, False)
            return
        track = self._playlist_tracks[self._playlist_index]
        title = track.title or track.filename
        text = f"{self._playlist_name} — {title} ({self._playlist_index + 1} of {len(self._playlist_tracks)})"
        self.analysis.set_playlist_context(
            text,
            self._playlist_index > 0,
            self._playlist_index < len(self._playlist_tracks) - 1,
        )

    def _on_load_failed(self, message: str) -> None:
        self._load_worker = None
        self._pending_queue = None
        self._pending_playlist_context = None
        self._set_loading(False)
        QMessageBox.warning(self, "Load failed", message)

    def _set_loading(self, loading: bool) -> None:
        self.home.setEnabled(not loading)
        self.queue_sidebar.setEnabled(not loading)
        self.transport.set_enabled_playback(not loading and self._app.engine is not None)
        if loading:
            self.statusBar().showMessage("Loading…")
        else:
            self.statusBar().clearMessage()

    def _on_mode_changed(self, row: int) -> None:
        if row == MODE_ANALYSIS:
            self._last_pos_ms = None  # force a playhead refresh on the next tick

    def _on_state_loaded(self) -> None:
        self.analysis.set_segments(self._state.segments)
        self.analysis.set_dirty(self._state.dirty)
        self.analysis.set_excluded(self._state.excluded)

    def _on_segments_changed(self) -> None:
        """A relabel/boundary/confirm/merge mutated the working list — merge
        also moves the selection, so re-pull both from EditorState."""
        self.analysis.set_segments(self._state.segments)
        self.analysis.set_selected(self._state.selection_index)

    def _show_edit_result(self, result: EditResult) -> None:
        if not result.ok:
            self.statusBar().showMessage(result.error or "Edit rejected.", 4000)

    # ------------------------------------------------------------------
    # selection binding (table <-> overlay, single source = EditorState)
    # ------------------------------------------------------------------

    def _on_selection_changed(self, index: int | None) -> None:
        self.analysis.set_selected(index)
        self._sync_loop_segment()

    def _on_timeline_clicked(self, position_ms: int) -> None:
        self._controller.seek(position_ms)
        self._state.select(self._state.segment_at(position_ms))

    # ------------------------------------------------------------------
    # loop-current-segment
    # ------------------------------------------------------------------

    def _on_loop_toggled(self, enabled: bool) -> None:
        self._controller.set_loop_enabled(enabled)
        self._sync_loop_segment()

    def _sync_loop_segment(self) -> None:
        index = self._state.selection_index
        segments = self._state.segments
        segment = segments[index] if index is not None else None
        self._controller.set_loop_segment(segment)

    # ------------------------------------------------------------------
    # timer
    # ------------------------------------------------------------------

    def _on_pos_tick(self) -> None:
        tracker = self._app.tracker
        if tracker is None:
            return
        position_ms = tracker.position_ms
        self.transport.set_position_ms(position_ms)
        if position_ms != self._last_pos_ms:
            if self._stack.currentWidget() is self.analysis:
                self.analysis.set_playhead_ms(position_ms)
            self._last_pos_ms = position_ms
        self._controller.check_loop(position_ms)
        self._check_track_finished(position_ms)

    def _check_track_finished(self, position_ms: int) -> None:
        engine, buffer = self._app.engine, self._app.buffer
        playing = engine is not None and engine.is_playing
        finished = (
            self._was_playing
            and not playing
            and buffer is not None
            and position_ms >= buffer.duration_ms - _TRACK_END_EPSILON_MS
        )
        self._was_playing = playing
        if finished:
            self._play_queue_track(self._queue.advance(manual=False))

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._load_worker is not None:
            self._load_worker.decoded.disconnect(self._on_load_decoded)
            self._load_worker.failed.disconnect(self._on_load_failed)
            self._load_worker.wait()
            self._load_worker = None
        self._pos_timer.stop()
        self._app.shutdown()
        super().closeEvent(event)
