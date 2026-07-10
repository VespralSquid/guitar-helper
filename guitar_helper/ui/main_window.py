"""QMainWindow: menu, docks, timers, and wiring between the widgets, the
Qt-free EditorState, and the Application composition root."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from guitar_helper.application import Application, NoSegmentsError
from guitar_helper.db.interfaces import Track
from guitar_helper.ui.controllers import PlaybackController
from guitar_helper.ui.models.qt_adapters import SegmentTableModel
from guitar_helper.ui.panels.library_panel import LibraryPanel
from guitar_helper.ui.state.editor_state import EditorState
from guitar_helper.ui.state.state_bridge import EditorStateBridge
from guitar_helper.ui.transport import TransportControls
from guitar_helper.ui.views.segment_overlay import SegmentOverlay
from guitar_helper.ui.views.waveform_view import WaveformView

_POS_TIMER_MS = 33


class MainWindow(QMainWindow):
    def __init__(self, application: Application, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Guitar Helper — Correction Editor")
        self._app = application
        self._controller = PlaybackController(application)

        tone_labels = tuple(p.tone_label for p in application.store.get_presets())
        self._state = EditorState(application.store, tone_labels)
        self._bridge = EditorStateBridge(self._state)

        self.transport = TransportControls()
        self.waveform = WaveformView()
        self.overlay = SegmentOverlay(self.waveform.plot_item)

        self.segment_model = SegmentTableModel()
        self.segment_table = QTableView()
        self.segment_table.setModel(self.segment_model)
        self.segment_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.segment_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.segment_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.library_panel = LibraryPanel(application.store)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.waveform, stretch=1)
        layout.addWidget(self.transport)
        self.setCentralWidget(central)

        library_dock = QDockWidget("Library", self)
        library_dock.setWidget(self.library_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, library_dock)

        segments_dock = QDockWidget("Segments", self)
        segments_dock.setWidget(self.segment_table)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, segments_dock)

        self._build_menu()
        self._wire_signals()

        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(_POS_TIMER_MS)
        self._pos_timer.timeout.connect(self._on_pos_tick)

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = file_menu.addAction("&Open…")
        open_action.triggered.connect(self._on_file_open)

    def _wire_signals(self) -> None:
        self.transport.playClicked.connect(self._controller.play)
        self.transport.pauseClicked.connect(self._controller.pause)
        self.transport.stopClicked.connect(self._controller.stop)
        self.transport.seekRequested.connect(self._controller.seek)
        self.transport.loopToggled.connect(self._on_loop_toggled)

        self.library_panel.trackChosen.connect(self._on_track_chosen)
        self.waveform.seekRequested.connect(self._on_waveform_clicked)

        self._bridge.loaded.connect(self._on_state_loaded)
        self._bridge.selectionChanged.connect(self._on_selection_changed)

        self.segment_table.selectionModel().selectionChanged.connect(
            self._on_table_selection_changed
        )

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------

    def _on_file_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open audio file", "", "Audio files (*.wav *.flac *.ogg *.mp3 *.m4a);;All files (*)"
        )
        if path:
            self._load_path(path)

    def _on_track_chosen(self, track: Track) -> None:
        if track.source_path:
            self._load_path(track.source_path)

    def _load_path(self, path: str) -> None:
        try:
            file_hash = self._app.load(path)
        except NoSegmentsError as exc:
            QMessageBox.warning(self, "No segments", str(exc))
            return

        self.transport.loop_checkbox.setChecked(False)
        self._state.load_track(file_hash)
        self.waveform.load(self._app.buffer)
        self.transport.set_duration_ms(self._app.buffer.duration_ms)
        self.transport.set_enabled_playback(True)
        self._pos_timer.start()

    def _on_state_loaded(self) -> None:
        segments = self._state.segments
        self.segment_model.set_segments(segments)
        self.overlay.set_segments(segments)

    # ------------------------------------------------------------------
    # selection binding (table <-> overlay, single source = EditorState)
    # ------------------------------------------------------------------

    def _on_selection_changed(self, index: int | None) -> None:
        self.overlay.set_selected(index)
        if index is None:
            self.segment_table.clearSelection()
        else:
            self.segment_table.selectRow(index)
        self._sync_loop_segment()

    def _on_table_selection_changed(self, *_args) -> None:
        rows = self.segment_table.selectionModel().selectedRows()
        self._state.select(rows[0].row() if rows else None)

    def _on_waveform_clicked(self, position_ms: int) -> None:
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
        self.waveform.set_playhead_ms(position_ms)
        self._controller.check_loop(position_ms)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._pos_timer.stop()
        self._app.shutdown()
        super().closeEvent(event)
