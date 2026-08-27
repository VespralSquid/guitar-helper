"""Shell QMainWindow: mode sidebar + stacked modes + persistent transport.

Owns the Application, the Qt-free EditorState (via its bridge), the load
worker, and the position timer. Each mode owns its widgets and internal
wiring (SRP rule from phase4-overhaul-plan.md §2); the shell only connects
mode signals to the controller/state."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import guitar_helper
from guitar_helper.analysis.factory import build_pipeline
from guitar_helper.analysis.pipeline import ManualCorrectionsExistError
from guitar_helper.application import Application, NoSegmentsError
from guitar_helper.config import resource_path
from guitar_helper.db.interfaces import Track
from guitar_helper.ui import theme
from guitar_helper.ui.analysis_worker import AnalysisWorker
from guitar_helper.ui.controllers import PlaybackController
from guitar_helper.ui.dialogs.add_songs import AddSongsDialog, AddSongsOptions
from guitar_helper.ui.dialogs.analysis_progress import AnalysisProgressDialog
from guitar_helper.ui.dialogs.update_prompt import UpdatePromptDialog
from guitar_helper.ui.editor.validation import EditResult
from guitar_helper.ui.load_worker import LoadWorker
from guitar_helper.ui.modes.analysis import AnalysisMode
from guitar_helper.ui.modes.home import HomeMode
from guitar_helper.ui.modes.output import OutputMode
from guitar_helper.ui.panels.queue_sidebar import QueueSidebar
from guitar_helper.ui.state.editor_state import EditorState
from guitar_helper.ui.state.queue_state import QueueState
from guitar_helper.ui.state.state_bridge import EditorStateBridge, QueueStateBridge
from guitar_helper.ui.transport import TransportControls
from guitar_helper.ui.update_worker import UpdateCheckWorker, UpdateDownloadWorker
from guitar_helper.update import UpdateError, launch_installer

_POS_TIMER_MS = 50
_DISPATCH_TIMER_MS = 100
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

        self._update_check: UpdateCheckWorker | None = None
        self._update_download: UpdateDownloadWorker | None = None
        self._update_dialog: UpdatePromptDialog | None = None
        self._verified_installer: str | None = None

        tone_labels = tuple(p.tone_label for p in application.store.get_presets())
        self._state = EditorState(application.store, tone_labels)
        self._bridge = EditorStateBridge(self._state)

        self._queue = QueueState()
        self._queue_bridge = QueueStateBridge(self._queue)

        self.home = HomeMode(application.store)
        self.analysis = AnalysisMode(tone_labels)
        self.output = OutputMode(application.store)
        self.output.set_dispatch_offset_ms(application.dispatch_offset_ms)
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

        self.midi_banner = QLabel()
        self.midi_banner.setObjectName("midiBanner")
        self.midi_banner.setWordWrap(True)
        self._refresh_midi_banner()

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.addWidget(self.midi_banner)
        row = QHBoxLayout()
        row.addWidget(self.sidebar)
        row.addWidget(self._stack, stretch=1)
        row.addWidget(self.queue_sidebar)
        outer.addLayout(row, stretch=1)
        outer.addWidget(self.transport)
        self.setCentralWidget(central)

        self._load_worker: LoadWorker | None = None
        self._analysis_worker: AnalysisWorker | None = None
        self._analysis_progress: AnalysisProgressDialog | None = None
        self._analysis_pipeline = None
        self._analysis_playlist_id: int | None = None
        self._resume_after_analysis = False
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
        # Runs only while Output is the visible mode — same reasoning as the
        # playhead repaint skip in O1: no work for a view nobody is looking at.
        # The log itself is a bounded deque, so nothing is lost while it sleeps.
        self._dispatch_timer = QTimer(self)
        self._dispatch_timer.setInterval(_DISPATCH_TIMER_MS)
        self._dispatch_timer.timeout.connect(self._on_dispatch_tick)

        self._build_menu()
        self._wire_signals()
        self.sidebar.setCurrentRow(MODE_HOME)

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        add_songs_action = file_menu.addAction("&Add songs…")
        add_songs_action.triggered.connect(lambda: self._on_add_songs_requested(None))
        open_action = file_menu.addAction("&Open…")
        open_action.triggered.connect(self._on_file_open)

        help_menu = self.menuBar().addMenu("&Help")
        guide_action = help_menu.addAction("&User guide")
        guide_action.triggered.connect(self._on_open_user_guide)
        update_action = help_menu.addAction("Check for &updates…")
        update_action.triggered.connect(lambda: self.check_for_updates(silent=False))
        about_action = help_menu.addAction("&About")
        about_action.triggered.connect(self._on_about)

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
        self.home.correctLabelsRequested.connect(self._on_correct_labels_requested)
        self.home.addSongsRequested.connect(self._on_add_songs_requested)
        self.home.trackRemoved.connect(self._on_track_removed)
        self.output.presetsChanged.connect(self._app.reload_presets)
        self.output.testSendRequested.connect(self._on_test_send)
        self.output.dispatchOffsetChanged.connect(self._app.set_dispatch_offset_ms)
        self.output.statusMessage.connect(lambda text: self.statusBar().showMessage(text, 4000))
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

    def _on_correct_labels_requested(
        self, playlist_name: str, tracks: list[Track], start_index: int
    ) -> None:
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
        except Exception as exc:  # noqa: BLE001
            # An exception escaping a Qt slot typically aborts the process, and
            # attach() rebuilds the whole runtime graph — anything from a device
            # error to a store failure can surface here, not just NoSegmentsError.
            # attach() validates before tearing down, so the previous track is
            # still playing and staying on it is the safe outcome.
            self._pending_queue = None
            self._pending_playlist_context = None
            title = "No segments" if isinstance(exc, NoSegmentsError) else "Cannot play this track"
            QMessageBox.warning(self, title, f"{name or 'Track'}: {exc}")
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
        busy = loading or self._analysis_worker is not None
        self.home.setEnabled(not busy)
        self.queue_sidebar.setEnabled(not busy)
        self.transport.set_enabled_playback(not loading and self._app.engine is not None)
        if loading:
            self.statusBar().showMessage("Loading…")
        else:
            self.statusBar().clearMessage()

    # ------------------------------------------------------------------
    # ingestion (analyse on AnalysisWorker, persist on the main thread)
    # ------------------------------------------------------------------

    def _on_add_songs_requested(self, playlist_id: int | None) -> None:
        if self._analysis_worker is not None:
            QMessageBox.information(
                self, "Analysis in progress",
                "Wait for the current analysis run to finish before starting another.",
            )
            return
        if self._load_worker is not None:
            return

        dialog = AddSongsDialog(
            self._app.store, self._app.config,
            preselected_playlist_id=playlist_id, parent=self,
        )
        if not dialog.exec():
            return
        options = dialog.options()
        if options is None or not options.paths:
            return
        self._start_analysis(options)

    def _start_analysis(self, options: AddSongsOptions) -> None:
        pipeline = build_pipeline(self._app.config, self._app.store)
        # precheck() hashes and reads the store, so it belongs here, not on the
        # worker. Hashing is ~0.05 s per file; a large folder makes this a
        # visible pause, hence the wait cursor.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage("Checking files…")
        try:
            to_analyse, skipped = self._classify_for_analysis(pipeline, options)
        finally:
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()

        if not to_analyse:
            QMessageBox.information(
                self, "Nothing to analyse",
                self._nothing_to_analyse_message(skipped),
            )
            return

        self._analysis_pipeline = pipeline
        self._analysis_playlist_id = options.target_playlist_id
        self._resume_after_analysis = False
        if options.pause_playback and self._app.engine is not None and self._app.engine.is_playing:
            self._controller.pause()
            self._resume_after_analysis = True

        progress = AnalysisProgressDialog(len(to_analyse), parent=self)
        for path, reason in skipped:
            progress.add_result(path.name, "skipped", reason)

        worker = AnalysisWorker(
            pipeline, to_analyse, {str(p): True for p in to_analyse}, parent=self
        )
        worker.progress.connect(self._on_analysis_progress)
        worker.fileDone.connect(self._on_analysis_file_done)
        worker.fileFailed.connect(self._on_analysis_file_failed)
        worker.fileSkipped.connect(self._on_analysis_file_skipped)
        worker.finished.connect(self._on_analysis_finished)
        progress.cancelRequested.connect(worker.cancel)

        self._analysis_worker = worker
        self._analysis_progress = progress
        self._set_loading(False)
        progress.show()
        worker.start()

    def _classify_for_analysis(
        self, pipeline, options: AddSongsOptions
    ) -> tuple[list[Path], list[tuple[Path, str]]]:
        """Apply the duplicate/corrections decision table (gui-analysis-pipeline
        -plan §2.5). A track with manual corrections is never re-analysed from
        the GUI — discard_corrections stays a deliberate CLI-only escape hatch."""
        to_analyse: list[Path] = []
        skipped: list[tuple[Path, str]] = []
        for path in options.paths:
            try:
                pre = pipeline.precheck(path)
            except Exception as exc:  # noqa: BLE001
                skipped.append((path, f"Could not read this file: {exc}"))
                continue
            if not pre.in_library:
                to_analyse.append(path)
            elif pre.corrected_count:
                plural = "" if pre.corrected_count == 1 else "s"
                skipped.append((path, (
                    f"Already in your library with {pre.corrected_count} corrected "
                    f"segment{plural}, which are never overwritten."
                )))
            elif options.reanalyse_existing:
                to_analyse.append(path)
            else:
                skipped.append((path, "Already analysed — tick “Re-analyse existing” to redo it."))
        return to_analyse, skipped

    def _nothing_to_analyse_message(self, skipped: list[tuple[Path, str]]) -> str:
        if not skipped:
            return "No files were selected."
        head = f"All {len(skipped)} selected file(s) were skipped:\n\n"
        shown = "\n".join(f"• {path.name} — {reason}" for path, reason in skipped[:10])
        rest = f"\n…and {len(skipped) - 10} more." if len(skipped) > 10 else ""
        return head + shown + rest

    def _on_analysis_progress(
        self, file_index: int, file_total: int, filename: str, stage: str
    ) -> None:
        if self._analysis_progress is not None:
            self._analysis_progress.set_progress(file_index, file_total, filename, stage)

    def _on_analysis_file_done(self, result: object) -> None:
        """Main thread: the sole SQLite owner writes what the worker computed."""
        progress = self._analysis_progress
        try:
            self._analysis_pipeline.persist(result)
        except ManualCorrectionsExistError:
            # The user corrected this track while its separation ran.
            if progress is not None:
                progress.add_result(
                    result.filename, "skipped",
                    "Corrections were made while this was analysing; kept them.",
                )
            return
        except Exception as exc:  # noqa: BLE001
            if progress is not None:
                progress.add_result(result.filename, "failed", f"Could not save: {exc}")
            return

        if self._analysis_playlist_id is not None:
            try:
                self._app.store.add_to_playlist(self._analysis_playlist_id, result.file_hash)
            except sqlite3.IntegrityError:
                pass  # already a member, or the playlist was deleted mid-run
        if progress is not None:
            progress.add_result(result.filename, "done", f"{len(result.segments)} segments")

    def _on_analysis_file_failed(self, path: str, message: str) -> None:
        if self._analysis_progress is not None:
            self._analysis_progress.add_result(Path(path).name, "failed", message)

    def _on_analysis_file_skipped(self, path: str, reason: str) -> None:
        if self._analysis_progress is not None:
            self._analysis_progress.add_result(Path(path).name, "skipped", reason)

    def _on_analysis_finished(self) -> None:
        worker, self._analysis_worker = self._analysis_worker, None
        if worker is not None:
            worker.deleteLater()
        if self._analysis_progress is not None:
            self._analysis_progress.set_finished()
        self._analysis_progress = None
        self._analysis_pipeline = None
        self._analysis_playlist_id = None
        if self._resume_after_analysis:
            self._resume_after_analysis = False
            self._controller.play()
        self.home.refresh()  # also closes review finding H4 (stale progress counts)
        self._set_loading(False)

    # ------------------------------------------------------------------
    # library removal
    # ------------------------------------------------------------------

    def _on_track_removed(self, file_hash: str) -> None:
        """The track the user just deleted may be the one loaded and playing —
        its segments are gone, so the dispatcher would look up nothing."""
        for index in range(len(self._queue.tracks) - 1, -1, -1):
            if self._queue.tracks[index].file_hash == file_hash:
                self._queue.remove(index)
        if self._state.file_hash != file_hash:
            return
        self._controller.stop()
        self._pos_timer.stop()
        self._state.clear()
        self.analysis.set_segments([])
        self.transport.set_enabled_playback(False)
        self._playlist_tracks = []
        self._playlist_index = 0
        self._update_playlist_header()

    # ------------------------------------------------------------------
    # MIDI availability + help
    # ------------------------------------------------------------------

    def _refresh_midi_banner(self) -> None:
        available = getattr(self._app, "midi_available", True)
        self.midi_banner.setVisible(not available)
        if not available:
            detail = getattr(self._app, "midi_error", None) or "No MIDI port was found."
            self.midi_banner.setText(
                f"MIDI disabled — {detail}  Segments and playback still work; program "
                f"changes are not sent. Start loopMIDI and restart to enable it."
            )

    def _on_open_user_guide(self) -> None:
        # resource_path, not __file__: in a frozen build this module lives under
        # _internal and the docs are a bundled data file, not a sibling of the
        # package.
        guide = resource_path("docs/user-guide.md")
        if guide.is_file() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(guide))):
            return
        QMessageBox.information(
            self, "User guide",
            f"The user guide is at:\n\n{guide}\n\nOpen it in any text or Markdown viewer.",
        )

    def _on_about(self) -> None:
        QMessageBox.about(
            self, "About Guitar Helper",
            f"Guitar Helper {guitar_helper.__version__} — offline guitar performance "
            "assistant.\n\n"
            "Analyses local audio, segments it by guitar tone, and fires MIDI "
            "Program Changes at tone boundaries during playback.",
        )

    # ------------------------------------------------------------------
    # updates
    # ------------------------------------------------------------------

    def check_for_updates(self, *, silent: bool = True) -> None:
        """Start a manifest check. `silent` suppresses the up-to-date and
        failure notices, which is what the launch-time check wants."""
        if self._update_check is not None and self._update_check.isRunning():
            return

        worker = UpdateCheckWorker(parent=self)
        self._update_check = worker
        worker.updateAvailable.connect(self._on_update_available)
        # A silent check swallows failures so an offline user is not nagged, but
        # it must still leave a trace: the release channel is currently a private
        # repo whose manifest URL 404s anonymously, and without this line an
        # updater that can never fire looks identical to one that found nothing.
        worker.failed.connect(
            lambda message: print(f"[update] check failed: {message}", file=sys.stderr)
        )
        if not silent:
            worker.upToDate.connect(
                lambda: QMessageBox.information(
                    self, "No update available",
                    f"Guitar Helper {guitar_helper.__version__} is the latest version.",
                )
            )
            worker.failed.connect(
                lambda message: QMessageBox.warning(self, "Update check failed", message)
            )
        worker.finished.connect(self._on_update_check_finished)
        worker.start()

    def _on_update_check_finished(self) -> None:
        # Drop the reference before deleteLater. Keeping it would leave a
        # wrapper around a deleted C++ object, and the isRunning() guard above
        # raises RuntimeError on the next check rather than returning False.
        worker, self._update_check = self._update_check, None
        if worker is not None:
            worker.deleteLater()

    def _on_update_available(self, manifest) -> None:
        dialog = UpdatePromptDialog(manifest, self)
        self._update_dialog = dialog
        dialog.downloadRequested.connect(lambda: self._start_update_download(manifest))
        dialog.cancelRequested.connect(self._cancel_update_download)
        dialog.installRequested.connect(self._install_update)
        dialog.finished.connect(lambda _: self._cancel_update_download())
        dialog.exec()
        self._update_dialog = None

    def _start_update_download(self, manifest) -> None:
        worker = UpdateDownloadWorker(manifest, parent=self)
        self._update_download = worker
        worker.progress.connect(self._on_update_progress)
        worker.ready.connect(self._on_update_ready)
        worker.failed.connect(self._on_update_failed)
        worker.finished.connect(self._on_update_download_finished)
        worker.start()

    def _on_update_download_finished(self) -> None:
        worker, self._update_download = self._update_download, None
        if worker is not None:
            worker.deleteLater()

    def _cancel_update_download(self) -> None:
        worker = self._update_download
        if worker is not None and worker.isRunning():
            worker.cancel()

    def _on_update_progress(self, downloaded: int, total: int) -> None:
        if self._update_dialog is not None:
            self._update_dialog.set_progress(downloaded, total)

    def _on_update_ready(self, path: str) -> None:
        self._verified_installer = path
        if self._update_dialog is not None:
            self._update_dialog.set_download_finished()

    def _on_update_failed(self, message: str) -> None:
        self._verified_installer = None
        if self._update_dialog is not None:
            self._update_dialog.set_failed(message)

    def _install_update(self) -> None:
        """Hand off to the installer and quit.

        Only ever launches the path `download_update` returned, which is the
        one that matched the manifest digest — never a path from the manifest
        or from the dialog.
        """
        installer = self._verified_installer
        if not installer:
            return
        try:
            launch_installer(installer)
        except UpdateError as exc:
            self._on_update_failed(str(exc))
            return
        if self._update_dialog is not None:
            self._update_dialog.accept()
        QApplication.instance().quit()

    def _on_mode_changed(self, row: int) -> None:
        if row == MODE_ANALYSIS:
            self._last_pos_ms = None  # force a playhead refresh on the next tick
        if row == MODE_OUTPUT:
            self.output.refresh_presets()
            self._on_dispatch_tick()  # show what the buffer holds, don't wait 100ms
            self._dispatch_timer.start()
        else:
            self._dispatch_timer.stop()

    def _on_dispatch_tick(self) -> None:
        self.output.append_events(self._app.dispatch_log.drain())
        self.output.set_active_preset(self._app.dispatch_log.last_send)

    def _on_test_send(self, pc_number: int) -> None:
        self._app.port.send_program_change(self._app.channel, pc_number)

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
        if self._analysis_worker is not None:
            # Cancel is honoured only at stage boundaries, so a separation in
            # flight still has to finish — better a slow close than a torn stem
            # cache or a QThread outliving its window.
            worker, self._analysis_worker = self._analysis_worker, None
            worker.fileDone.disconnect(self._on_analysis_file_done)
            worker.finished.disconnect(self._on_analysis_finished)
            worker.cancel()
            worker.wait()
        if self._update_download is not None:
            # A download aborts cheaply and the partial file is discarded, so
            # unlike analysis this one does not have to run to completion — but
            # the thread still has to be joined before its window goes away.
            worker, self._update_download = self._update_download, None
            worker.cancel()
            worker.wait()
        if self._update_check is not None:
            worker, self._update_check = self._update_check, None
            worker.wait()
        self._pos_timer.stop()
        self._dispatch_timer.stop()
        self._app.shutdown()
        super().closeEvent(event)
