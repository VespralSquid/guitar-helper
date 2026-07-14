from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget


def _format_ms(ms: int) -> str:
    total_s = max(0, ms) // 1000
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


class TransportControls(QWidget):
    playClicked = Signal()
    pauseClicked = Signal()
    stopClicked = Signal()
    nextClicked = Signal()
    previousClicked = Signal()
    seekRequested = Signal(int)
    loopToggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration_ms = 0
        self._slider_pressed = False

        self.previous_button = QPushButton("Prev")
        self.play_button = QPushButton("Play")
        self.pause_button = QPushButton("Pause")
        self.stop_button = QPushButton("Stop")
        self.next_button = QPushButton("Next")
        self.loop_checkbox = QCheckBox("Loop segment")
        self.position_label = QLabel("00:00 / 00:00")
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)

        layout = QHBoxLayout(self)
        layout.addWidget(self.previous_button)
        layout.addWidget(self.play_button)
        layout.addWidget(self.pause_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.next_button)
        layout.addWidget(self.loop_checkbox)
        layout.addWidget(self.seek_slider, stretch=1)
        layout.addWidget(self.position_label)

        self.play_button.clicked.connect(self.playClicked)
        self.pause_button.clicked.connect(self.pauseClicked)
        self.stop_button.clicked.connect(self.stopClicked)
        self.next_button.clicked.connect(self.nextClicked)
        self.previous_button.clicked.connect(self.previousClicked)
        self.loop_checkbox.toggled.connect(self.loopToggled)
        self.seek_slider.sliderPressed.connect(self._on_slider_pressed)
        self.seek_slider.sliderReleased.connect(self._on_slider_released)

        self.set_enabled_playback(False)

    def set_enabled_playback(self, enabled: bool) -> None:
        widgets = (
            self.previous_button,
            self.play_button,
            self.pause_button,
            self.stop_button,
            self.next_button,
            self.seek_slider,
        )
        for widget in widgets:
            widget.setEnabled(enabled)

    def set_duration_ms(self, duration_ms: int) -> None:
        self._duration_ms = duration_ms
        self.seek_slider.setRange(0, duration_ms)
        self.set_position_ms(0)

    def set_position_ms(self, position_ms: int) -> None:
        if not self._slider_pressed:
            self.seek_slider.setValue(position_ms)
        self.position_label.setText(f"{_format_ms(position_ms)} / {_format_ms(self._duration_ms)}")

    def _on_slider_pressed(self) -> None:
        self._slider_pressed = True

    def _on_slider_released(self) -> None:
        self._slider_pressed = False
        self.seekRequested.emit(self.seek_slider.value())
