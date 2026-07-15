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


def boundary_at_x(
    x: float, segments: list[Segment], duration_ms: int, width: int, hit_px: int
) -> int | None:
    """Index of the interior boundary (between segments[i] and segments[i+1])
    within hit_px of x, or None. Track start/end are never boundaries here —
    there's no segment on the other side to move with them."""
    for i in range(len(segments) - 1):
        bx = ms_to_x(segments[i].end_ms, duration_ms, width)
        if abs(x - bx) <= hit_px:
            return i
    return None


class SegmentTimeline(QWidget):
    seekRequested = Signal(int)  # position_ms
    boundaryEditRequested = Signal(int, int)  # left_index, new_ms

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
        self._dragging_boundary: int | None = None
        self._drag_preview_ms: int | None = None

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
            start_ms = segment.start_ms
            end_ms = segment.end_ms
            if self._dragging_boundary is not None:
                if i == self._dragging_boundary:
                    end_ms = self._drag_preview_ms
                elif i == self._dragging_boundary + 1:
                    start_ms = self._drag_preview_ms
            x0 = ms_to_x(start_ms, self._duration_ms, width)
            x1 = ms_to_x(end_ms, self._duration_ms, width)
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
        if self._duration_ms <= 0 or event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        boundary = boundary_at_x(
            x, self._segments, self._duration_ms, self.width(), theme.BOUNDARY_HIT_PX
        )
        if boundary is not None:
            self._dragging_boundary = boundary
            self._drag_preview_ms = self._segments[boundary].end_ms
            return
        self.seekRequested.emit(x_to_ms(x, self._duration_ms, self.width()))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._duration_ms <= 0:
            return
        x = event.position().x()
        if self._dragging_boundary is not None:
            left = self._segments[self._dragging_boundary]
            right = self._segments[self._dragging_boundary + 1]
            candidate = x_to_ms(x, self._duration_ms, self.width())
            self._drag_preview_ms = max(left.start_ms + 1, min(candidate, right.end_ms - 1))
            self.update()
            return
        boundary = boundary_at_x(
            x, self._segments, self._duration_ms, self.width(), theme.BOUNDARY_HIT_PX
        )
        self.setCursor(
            Qt.CursorShape.SizeHorCursor if boundary is not None
            else Qt.CursorShape.PointingHandCursor
        )
        ms = x_to_ms(x, self._duration_ms, self.width())
        tone = next(
            (s.tone_label for s in self._segments if s.start_ms <= ms < s.end_ms), None
        )
        self.setToolTip(tone or "")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._dragging_boundary is None:
            return
        left_index = self._dragging_boundary
        new_ms = self._drag_preview_ms
        self._dragging_boundary = None
        self._drag_preview_ms = None
        if new_ms is not None:
            self.boundaryEditRequested.emit(left_index, new_ms)
        self.update()
