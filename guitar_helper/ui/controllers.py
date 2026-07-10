"""Transport intent -> Application, keeping transport widgets dumb.

Also owns loop-current-segment: when armed, `check_loop` (polled by
MainWindow's position timer) re-seeks to the segment start once playback
crosses its end, entirely from the main thread.
"""
from __future__ import annotations

from guitar_helper.application import Application
from guitar_helper.db.interfaces import Segment


class PlaybackController:
    def __init__(self, application: Application) -> None:
        self._app = application
        self._loop_enabled = False
        self._loop_segment: Segment | None = None

    def play(self) -> None:
        self._app.play()

    def pause(self) -> None:
        self._app.pause()

    def stop(self) -> None:
        self._app.stop()

    def seek(self, position_ms: int) -> None:
        self._app.seek(position_ms)

    def set_loop_enabled(self, enabled: bool) -> None:
        self._loop_enabled = enabled

    def set_loop_segment(self, segment: Segment | None) -> None:
        self._loop_segment = segment

    def check_loop(self, position_ms: int) -> None:
        if not self._loop_enabled or self._loop_segment is None:
            return
        if position_ms >= self._loop_segment.end_ms:
            self.seek(self._loop_segment.start_ms)
