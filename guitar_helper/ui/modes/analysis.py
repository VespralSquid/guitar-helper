"""Analysis mode: segment-colored timeline + segment table (AC8: segments
over waveform — the waveform is gone entirely, replaced by SegmentTimeline).
Owns its widgets and internal wiring; talks to the shell only through
signals and plain methods — no EditorState or Application access here.
Edit controls (relabel/confirm/merge/exclude/save/discard) forward user
intent as signals; the shell decides what's valid by calling EditorState."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
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

    relabelRequested = Signal(int, str)
    confirmRequested = Signal(int)
    confirmAllRequested = Signal()
    mergeRequested = Signal(int)
    excludeToggled = Signal(bool)
    saveRequested = Signal()
    discardRequested = Signal()
    boundaryEditRequested = Signal(int, int)  # left_index, new_ms (from timeline)

    analyzePrevRequested = Signal()
    analyzeNextRequested = Signal()

    def __init__(self, tone_labels: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected_index: int | None = None
        self.timeline = SegmentTimeline()

        self.header_label = QLabel()
        self.prev_song_button = QPushButton("◀ Prev song")
        self.next_song_button = QPushButton("Next song ▶")
        self.prev_song_button.setEnabled(False)
        self.next_song_button.setEnabled(False)

        self.segment_model = SegmentTableModel()
        self.segment_table = QTableView()
        self.segment_table.setModel(self.segment_model)
        self.segment_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.segment_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.segment_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        self.relabel_combo = QComboBox()
        self.relabel_combo.addItems(list(tone_labels))
        self.confirm_button = QPushButton("Confirm")
        self.confirm_all_button = QPushButton("Confirm All")
        self.merge_button = QPushButton("Merge")
        self.exclude_checkbox = QCheckBox("Exclude from calibration")
        self._set_selection_controls_enabled(False)

        edit_toolbar = QHBoxLayout()
        edit_toolbar.addWidget(QLabel("Relabel:"))
        edit_toolbar.addWidget(self.relabel_combo)
        edit_toolbar.addWidget(self.confirm_button)
        edit_toolbar.addWidget(self.confirm_all_button)
        edit_toolbar.addWidget(self.merge_button)
        edit_toolbar.addStretch(1)
        edit_toolbar.addWidget(self.exclude_checkbox)

        self.dirty_label = QLabel()
        self.discard_button = QPushButton("Discard")
        self.save_button = QPushButton("Save")
        self.discard_button.setEnabled(False)
        self.save_button.setEnabled(False)

        bottom_bar = QHBoxLayout()
        bottom_bar.addWidget(self.dirty_label)
        bottom_bar.addStretch(1)
        bottom_bar.addWidget(self.discard_button)
        bottom_bar.addWidget(self.save_button)

        header_bar = QHBoxLayout()
        header_bar.addWidget(self.prev_song_button)
        header_bar.addWidget(self.header_label, stretch=1)
        header_bar.addWidget(self.next_song_button)

        layout = QVBoxLayout(self)
        layout.addLayout(header_bar)
        layout.addWidget(self.timeline)
        layout.addLayout(edit_toolbar)
        layout.addWidget(self.segment_table, stretch=1)
        layout.addLayout(bottom_bar)

        self.prev_song_button.clicked.connect(self.analyzePrevRequested)
        self.next_song_button.clicked.connect(self.analyzeNextRequested)

        self.timeline.seekRequested.connect(self.seekRequested)
        self.timeline.boundaryEditRequested.connect(self.boundaryEditRequested)
        self.segment_table.selectionModel().selectionChanged.connect(self._on_table_selection)

        self.relabel_combo.textActivated.connect(self._on_relabel_activated)
        self.confirm_button.clicked.connect(self._on_confirm_clicked)
        self.confirm_all_button.clicked.connect(self.confirmAllRequested)
        self.merge_button.clicked.connect(self._on_merge_clicked)
        self.exclude_checkbox.toggled.connect(self.excludeToggled)
        self.save_button.clicked.connect(self.saveRequested)
        self.discard_button.clicked.connect(self.discardRequested)

    # ------------------------------------------------------------------
    # state -> view
    # ------------------------------------------------------------------

    def set_duration_ms(self, duration_ms: int) -> None:
        self.timeline.set_duration_ms(duration_ms)

    def set_segments(self, segments: list[Segment]) -> None:
        self.segment_model.set_segments(segments)
        self.timeline.set_segments(segments)
        self.confirm_all_button.setEnabled(bool(segments))

    def set_selected(self, index: int | None) -> None:
        self._selected_index = index
        self.timeline.set_selected(index)
        if index is None:
            self.segment_table.clearSelection()
        else:
            self.segment_table.selectRow(index)
        self._set_selection_controls_enabled(index is not None)
        if index is not None:
            label = self.segment_model.segment_at_row(index).tone_label
            self.relabel_combo.blockSignals(True)
            self.relabel_combo.setCurrentText(label)
            self.relabel_combo.blockSignals(False)

    def set_playhead_ms(self, position_ms: int) -> None:
        self.timeline.set_playhead_ms(position_ms)

    def set_playlist_context(self, header_text: str, has_prev: bool, has_next: bool) -> None:
        self.header_label.setText(header_text)
        self.prev_song_button.setEnabled(has_prev)
        self.next_song_button.setEnabled(has_next)

    def set_dirty(self, dirty: bool) -> None:
        self.dirty_label.setText("Unsaved changes" if dirty else "")
        self.save_button.setEnabled(dirty)
        self.discard_button.setEnabled(dirty)

    def set_excluded(self, excluded: bool) -> None:
        self.exclude_checkbox.blockSignals(True)
        self.exclude_checkbox.setChecked(excluded)
        self.exclude_checkbox.blockSignals(False)

    def _set_selection_controls_enabled(self, enabled: bool) -> None:
        self.relabel_combo.setEnabled(enabled)
        self.confirm_button.setEnabled(enabled)
        self.merge_button.setEnabled(enabled)

    # ------------------------------------------------------------------
    # view -> intent
    # ------------------------------------------------------------------

    def _on_table_selection(self, *_args) -> None:
        rows = self.segment_table.selectionModel().selectedRows()
        self.rowSelected.emit(rows[0].row() if rows else None)

    def _on_relabel_activated(self, label: str) -> None:
        if self._selected_index is not None:
            self.relabelRequested.emit(self._selected_index, label)

    def _on_confirm_clicked(self) -> None:
        if self._selected_index is not None:
            self.confirmRequested.emit(self._selected_index)

    def _on_merge_clicked(self) -> None:
        if self._selected_index is not None:
            self.mergeRequested.emit(self._selected_index)
