"""Segment-colored seek timeline — replaces the pyqtgraph waveform.

One `paintEvent` draws a handful of tone-colored rects plus a playhead line,
and `set_playhead_ms` repaints only when the playhead crosses a pixel column.
This keeps main-thread paint work near zero during playback, which is the
ISSUE-005 fix: the pyqtgraph scene repaint at 20 Hz starved the audio
callback of the GIL whenever Analysis mode was visible."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from guitar_helper.db.interfaces import Segment
from guitar_helper.ui import theme


def ms_to_x(ms: int, duration_ms: int, width: int) -> int:
    if duration_ms <= 0:
        return 0
    return round(max(0, min(ms, duration_ms)) / duration_ms * width)


def x_to_ms(x: float, duration_ms: int, width: int) -> int:
    if width <= 0:
        return 0
    return int(max(0.0, min(x, width)) / width * duration_ms)


class SegmentTimeline(QWidget):
    seekRequested = Signal(int)  # position_ms

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(theme.TIMELINE_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._duration_ms = 0
        self._segments: list[Segment] = []
        self._selected: int | None = None
        self._playhead_ms = 0
        self._last_playhead_x = -1

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------

    def set_duration_ms(self, duration_ms: int) -> None:
        self._duration_ms = duration_ms
        self._playhead_ms = 0
        self._last_playhead_x = -1
        self.update()

    def set_segments(self, segments: list[Segment]) -> None:
        self._segments = list(segments)
        self.update()

    def set_selected(self, index: int | None) -> None:
        if index != self._selected:
            self._selected = index
            self.update()

    def set_playhead_ms(self, position_ms: int) -> None:
        self._playhead_ms = position_ms
        x = ms_to_x(position_ms, self._duration_ms, self.width())
        if x != self._last_playhead_x:
            self._last_playhead_x = x
            self.update()

    # ------------------------------------------------------------------
    # painting / input
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        height = self.height()
        painter.fillRect(0, 0, self.width(), height, QColor(theme.TIMELINE_BG))
        if self._duration_ms <= 0:
            painter.end()
            return

        width = self.width()
        selected_rect = None
        for i, segment in enumerate(self._segments):
            x0 = ms_to_x(segment.start_ms, self._duration_ms, width)
            x1 = ms_to_x(segment.end_ms, self._duration_ms, width)
            selected = i == self._selected
            painter.fillRect(
                x0, 0, max(1, x1 - x0), height,
                theme.tone_qcolor(segment.tone_label, selected=selected),
            )
            if selected:
                selected_rect = (x0, x1)

        if selected_rect is not None:
            painter.setPen(QPen(QColor(theme.TIMELINE_SELECTED_BORDER), 1))
            x0, x1 = selected_rect
            painter.drawRect(x0, 0, max(1, x1 - x0) - 1, height - 1)

        playhead_x = ms_to_x(self._playhead_ms, self._duration_ms, width)
        painter.fillRect(
            playhead_x - theme.PLAYHEAD_WIDTH // 2, 0,
            theme.PLAYHEAD_WIDTH, height,
            QColor(theme.PLAYHEAD_COLOR),
        )
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._duration_ms > 0 and event.button() == Qt.MouseButton.LeftButton:
            self.seekRequested.emit(
                x_to_ms(event.position().x(), self._duration_ms, self.width())
            )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._duration_ms <= 0:
            return
        ms = x_to_ms(event.position().x(), self._duration_ms, self.width())
        tone = next(
            (s.tone_label for s in self._segments if s.start_ms <= ms < s.end_ms), None
        )
        self.setToolTip(tone or "")
