from __future__ import annotations

import threading

from guitar_helper.db.interfaces import Segment


class PositionTracker:
    """Thread-safe playback position.

    The audio thread writes the frame cursor; the MIDI/UI threads read
    position_ms. A lock guards the single int so reads never see a torn value
    and the conversion to ms is consistent.
    """

    def __init__(self, sr: int) -> None:
        self._sr = sr
        self._cursor = 0
        self._lock = threading.Lock()

    def set_cursor(self, frame: int) -> None:
        with self._lock:
            self._cursor = frame

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._cursor

    @property
    def position_ms(self) -> int:
        with self._lock:
            return int(self._cursor / self._sr * 1000)

    @staticmethod
    def ms_until_next_boundary(position_ms: int, segments: list[Segment]) -> int | None:
        """ms from position_ms to the next segment start ahead of it, or None."""
        starts = sorted(s.start_ms for s in segments if s.start_ms > position_ms)
        return starts[0] - position_ms if starts else None
