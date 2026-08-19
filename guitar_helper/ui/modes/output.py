"""Output mode: MIDI configuration and the live dispatch log (O4).

Three jobs, all diagnostic-first — this is the panel you open when the amp
does not switch (ISSUE-004):

  * preset table — tone -> Program Change, the dispatcher's single source of
    truth; editable names so rows can be labelled with the actual amp patch,
    plus a test-send that proves the loopMIDI -> host -> plugin chain responds
    without playing a track;
  * dispatch log — every decision the dispatcher makes, holds included, so a
    silent amp is distinguishable from a silent dispatcher;
  * dispatch offset — the single calibration knob from
    docs/Report/latency-calibration-analysis.md.

Follows the HomeMode pattern: the mode owns its store reads/writes (main
thread) and emits signals only for what needs the Application — the MIDI port,
the live dispatcher's preset map, and the persisted offset.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from guitar_helper.db.interfaces import Preset
from guitar_helper.db.schema import default_preset_map
from guitar_helper.playback.dispatch_log import MAX_EVENTS, SEND, DispatchEvent
from guitar_helper.playback.midi_dispatcher import DEFAULT_LOOKAHEAD_MS
from guitar_helper.ui import theme
from guitar_helper.ui.editor.preset_validation import (
    NO_DISPATCH,
    apply_name,
    apply_pc,
    validate_name,
    validate_pc,
)
from guitar_helper.ui.models.qt_adapters import PresetTableModel

_OFFSET_MIN_MS = -500
_OFFSET_MAX_MS = 500
_NO_ACTIVE_PRESET = "Active preset: — (nothing dispatched yet)"

_KIND_TEXT = {
    SEND: "sent",
    "hold": "hold",
    "unmapped": "UNMAPPED",
    "gap": "gap",
}


def format_position(ms: int) -> str:
    total_s, millis = divmod(max(0, ms), 1000)
    return f"{total_s // 60:d}:{total_s % 60:02d}.{millis:03d}"


def format_event(event: DispatchEvent) -> str:
    kind = _KIND_TEXT.get(event.kind, event.kind)
    tone = event.tone or "—"
    if event.kind == SEND:
        detail = f"{tone} → PC {event.pc}"
    elif event.kind == "gap":
        detail = "between segments — holding"
    elif event.kind == "unmapped":
        detail = f"{tone} has no preset row — holding"
    elif event.pc == NO_DISPATCH or event.pc is None:
        detail = f"{tone} — holding preset, no dispatch"
    else:
        detail = f"{tone} → PC {event.pc} already active"
    return f"{format_position(event.position_ms):>9}  {kind:<9} {detail}"


def preset_divergences(
    presets: Sequence[Preset], defaults: Mapping[str, int]
) -> list[tuple[str, int, int]]:
    """(tone, live_pc, default_pc) for every default tone whose PC has drifted."""
    return [
        (p.tone_label, p.pc_number, defaults[p.tone_label])
        for p in presets
        if p.tone_label in defaults and p.pc_number != defaults[p.tone_label]
    ]


def format_divergence_banner(divergences: Sequence[tuple[str, int, int]]) -> str:
    """One line naming the diverging tones and both PC values."""
    parts = [f"{tone} is PC {live} (default {default})" for tone, live, default in divergences]
    return "Preset map has drifted from the defaults: " + "; ".join(parts)


class OutputMode(QWidget):
    presetsChanged = Signal()          # a preset row was written; refresh the dispatcher
    testSendRequested = Signal(int)    # pc_number, straight to the MIDI port
    dispatchOffsetChanged = Signal(int)
    statusMessage = Signal(str)        # validation rejections, for the status bar

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._applied_offset_ms = DEFAULT_LOOKAHEAD_MS
        self._previous_offset_ms: int | None = None

        self.preset_model = PresetTableModel()
        self.preset_table = QTableView()
        self.preset_table.setModel(self.preset_model)
        self.preset_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.preset_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.preset_table.horizontalHeader().setStretchLastSection(True)
        self.test_send_button = QPushButton("Send test PC")
        self.test_send_button.setEnabled(False)
        self.test_send_button.setToolTip(
            "Send this row's Program Change now — verifies loopMIDI, the host and "
            "the plugin without playing a track."
        )
        self.divergence_banner = QLabel()
        self.divergence_banner.setWordWrap(True)
        self.divergence_banner.setVisible(False)
        self.reset_presets_button = QPushButton("Reset to defaults")
        self.reset_presets_button.setVisible(False)

        preset_box = QGroupBox("Tone → Program Change")
        preset_layout = QVBoxLayout(preset_box)
        preset_layout.addWidget(self.divergence_banner)
        preset_layout.addWidget(self.preset_table, stretch=1)
        preset_buttons = QHBoxLayout()
        preset_buttons.addWidget(self.test_send_button)
        preset_buttons.addWidget(self.reset_presets_button)
        preset_buttons.addStretch(1)
        preset_layout.addLayout(preset_buttons)

        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(_OFFSET_MIN_MS, _OFFSET_MAX_MS)
        self.offset_spin.setSuffix(" ms")
        self.offset_spin.setValue(DEFAULT_LOOKAHEAD_MS)
        self.apply_offset_button = QPushButton("Apply")
        self.revert_offset_button = QPushButton("Revert")
        self.revert_offset_button.setEnabled(False)
        self.offset_label = QLabel()

        offset_box = QGroupBox("Dispatch offset (calibration)")
        offset_layout = QVBoxLayout(offset_box)
        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("Send each Program Change"))
        offset_row.addWidget(self.offset_spin)
        offset_row.addWidget(QLabel("before the tone boundary"))
        offset_row.addWidget(self.apply_offset_button)
        offset_row.addWidget(self.revert_offset_button)
        offset_row.addStretch(1)
        offset_layout.addLayout(offset_row)
        offset_layout.addWidget(self.offset_label)

        self.active_label = QLabel(_NO_ACTIVE_PRESET)
        self.log_list = QListWidget()
        self.log_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.clear_log_button = QPushButton("Clear log")

        log_box = QGroupBox("Dispatch log")
        log_layout = QVBoxLayout(log_box)
        log_layout.addWidget(self.active_label)
        log_layout.addWidget(self.log_list, stretch=1)
        log_buttons = QHBoxLayout()
        log_buttons.addStretch(1)
        log_buttons.addWidget(self.clear_log_button)
        log_layout.addLayout(log_buttons)

        top = QHBoxLayout()
        top.addWidget(preset_box, stretch=1)
        top.addWidget(offset_box, stretch=1)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(log_box, stretch=1)

        self.preset_model.pcEditRequested.connect(self._on_pc_edit)
        self.preset_model.nameEditRequested.connect(self._on_name_edit)
        self.preset_model.editRejected.connect(self.statusMessage)
        self.preset_table.selectionModel().selectionChanged.connect(self._on_preset_selection)
        self.test_send_button.clicked.connect(self._on_test_send)
        self.reset_presets_button.clicked.connect(self._on_reset_presets)
        self.apply_offset_button.clicked.connect(self._on_apply_offset)
        self.revert_offset_button.clicked.connect(self._on_revert_offset)
        self.clear_log_button.clicked.connect(self.log_list.clear)

        self.refresh_presets()
        self._update_offset_label()

    # ------------------------------------------------------------------
    # presets
    # ------------------------------------------------------------------

    def refresh_presets(self) -> None:
        self.preset_model.set_presets(self._store.get_presets())
        self._on_preset_selection()
        diverged = preset_divergences(self.preset_model.presets, default_preset_map())
        self.divergence_banner.setText(format_divergence_banner(diverged))
        self.divergence_banner.setVisible(bool(diverged))
        self.reset_presets_button.setVisible(bool(diverged))

    def _on_reset_presets(self) -> None:
        self._store.reset_presets_to_defaults()
        self.refresh_presets()
        self.presetsChanged.emit()
        self.statusMessage.emit("Preset map reset to defaults.")

    def _selected_preset(self):
        rows = self.preset_table.selectionModel().selectedRows()
        return self.preset_model.preset_at_row(rows[0].row()) if rows else None

    def _on_preset_selection(self, *_args) -> None:
        preset = self._selected_preset()
        self.test_send_button.setEnabled(preset is not None and preset.pc_number >= 0)

    def _on_pc_edit(self, tone_label: str, pc_number: int) -> None:
        preset = self._preset_for(tone_label)
        if preset is None:
            return
        result = validate_pc(self.preset_model.presets, tone_label, pc_number)
        if not result.ok:
            self.statusMessage.emit(result.error or "Preset edit rejected.")
            return
        self._store.save_preset(apply_pc(preset, pc_number))
        self.refresh_presets()
        self.presetsChanged.emit()

    def _on_name_edit(self, tone_label: str, name: str) -> None:
        preset = self._preset_for(tone_label)
        if preset is None:
            return
        result = validate_name(name)
        if not result.ok:
            self.statusMessage.emit(result.error or "Preset edit rejected.")
            return
        self._store.save_preset(apply_name(preset, name))
        self.refresh_presets()
        self.presetsChanged.emit()

    def _preset_for(self, tone_label: str):
        return next(
            (p for p in self.preset_model.presets if p.tone_label == tone_label), None
        )

    def _on_test_send(self) -> None:
        preset = self._selected_preset()
        if preset is not None and preset.pc_number >= 0:
            self.testSendRequested.emit(preset.pc_number)
            self.statusMessage.emit(
                f"Sent PC {preset.pc_number} ({preset.preset_name}) to the MIDI port."
            )

    # ------------------------------------------------------------------
    # dispatch offset
    # ------------------------------------------------------------------

    def set_dispatch_offset_ms(self, value: int) -> None:
        """Seed the control from the persisted value without re-emitting."""
        self._applied_offset_ms = value
        self.offset_spin.setValue(value)
        self._update_offset_label()

    def _on_apply_offset(self) -> None:
        value = self.offset_spin.value()
        if value == self._applied_offset_ms:
            return
        self._previous_offset_ms = self._applied_offset_ms
        self._applied_offset_ms = value
        self.revert_offset_button.setEnabled(True)
        self._update_offset_label()
        self.dispatchOffsetChanged.emit(value)

    def _on_revert_offset(self) -> None:
        """Undo the last Apply — the safety net for a calibration that turned
        out worse by ear, without needing to remember the old number."""
        if self._previous_offset_ms is None:
            return
        value = self._previous_offset_ms
        self._previous_offset_ms = None
        self._applied_offset_ms = value
        self.offset_spin.setValue(value)
        self.revert_offset_button.setEnabled(False)
        self._update_offset_label()
        self.dispatchOffsetChanged.emit(value)

    def _update_offset_label(self) -> None:
        text = f"In effect: {self._applied_offset_ms} ms (default {DEFAULT_LOOKAHEAD_MS} ms)."
        if self._previous_offset_ms is not None:
            text += f" Revert restores {self._previous_offset_ms} ms."
        self.offset_label.setText(text)

    # ------------------------------------------------------------------
    # dispatch log
    # ------------------------------------------------------------------

    def append_events(self, events: list[DispatchEvent]) -> None:
        for event in events:
            item = QListWidgetItem(format_event(event))
            if event.tone:
                item.setForeground(theme.tone_qcolor(event.tone, selected=True))
            self.log_list.addItem(item)
        while self.log_list.count() > MAX_EVENTS:
            self.log_list.takeItem(0)
        if events:
            self.log_list.scrollToBottom()

    def set_active_preset(self, event: DispatchEvent | None) -> None:
        if event is None:
            self.active_label.setText(_NO_ACTIVE_PRESET)
            return
        self.active_label.setText(
            f"Active preset: PC {event.pc} ({event.tone}) since {format_position(event.position_ms)}"
        )
