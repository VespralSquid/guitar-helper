"""Qt-free play-queue state: order, current index, shuffle, repeat.

Import guard: no `PySide6`/`pyqtgraph` here — unit-tested without a
QApplication. `QueueStateBridge` (state_bridge.py) adapts it to Qt signals.
The queue is session state only; playlists are what persist (IPlaylistStore).
"""
from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from guitar_helper.db.interfaces import Track

REPEAT_MODES = ("off", "all", "one")


@dataclass
class QueueEvent:
    kind: Literal["queue", "current"]


class QueueState:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._tracks: list[Track] = []      # play order (shuffled when shuffle is on)
        self._original: list[Track] = []    # insertion order, for stable un-shuffle
        self._index: int | None = None
        self._shuffle = False
        self._repeat = "off"
        self._subscribers: list[Callable[[QueueEvent], None]] = []

    # ------------------------------------------------------------------
    # read accessors
    # ------------------------------------------------------------------

    @property
    def tracks(self) -> list[Track]:
        return list(self._tracks)

    @property
    def current_index(self) -> int | None:
        return self._index

    @property
    def current_track(self) -> Track | None:
        return self._tracks[self._index] if self._index is not None else None

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @property
    def repeat(self) -> str:
        return self._repeat

    # ------------------------------------------------------------------
    # queue construction
    # ------------------------------------------------------------------

    def set_queue(self, tracks: list[Track], start_index: int = 0) -> None:
        self._original = list(tracks)
        if not tracks:
            self._tracks = []
            self._index = None
        else:
            start_index = max(0, min(start_index, len(tracks) - 1))
            if self._shuffle:
                self._tracks = self._shuffled_from(tracks, tracks[start_index])
                self._index = 0
            else:
                self._tracks = list(tracks)
                self._index = start_index
        self._emit(QueueEvent("queue"))
        self._emit(QueueEvent("current"))

    def clear(self) -> None:
        self.set_queue([])

    # ------------------------------------------------------------------
    # traversal
    # ------------------------------------------------------------------

    def advance(self, manual: bool = False) -> Track | None:
        """Next track to play, or None when the queue is exhausted.

        Track-finished advancement honors repeat-one; a manual Next always
        moves on (standard player behavior)."""
        if self._index is None:
            return None
        if self._repeat == "one" and not manual:
            return self.current_track
        if self._index + 1 < len(self._tracks):
            self._index += 1
        elif self._repeat in ("all", "one"):
            self._index = 0
        else:
            return None
        self._emit(QueueEvent("current"))
        return self.current_track

    def previous(self) -> Track | None:
        """Previous track, or the current one when already at the start."""
        if self._index is None:
            return None
        if self._index > 0:
            self._index -= 1
            self._emit(QueueEvent("current"))
        return self.current_track

    def play_at(self, index: int) -> Track | None:
        if not (0 <= index < len(self._tracks)):
            return None
        self._index = index
        self._emit(QueueEvent("current"))
        return self.current_track

    # ------------------------------------------------------------------
    # manipulation
    # ------------------------------------------------------------------

    def move(self, from_index: int, to_index: int) -> None:
        n = len(self._tracks)
        if not (0 <= from_index < n and 0 <= to_index < n) or from_index == to_index:
            return
        current = self.current_track
        track = self._tracks.pop(from_index)
        self._tracks.insert(to_index, track)
        if current is not None:
            self._index = self._tracks.index(current)
        self._emit(QueueEvent("queue"))

    def remove(self, index: int) -> None:
        if not (0 <= index < len(self._tracks)):
            return
        was_current = index == self._index
        del self._tracks[index]
        if not self._tracks:
            self._index = None
        elif self._index is not None:
            if index < self._index:
                self._index -= 1
            elif was_current:
                self._index = min(self._index, len(self._tracks) - 1)
        self._emit(QueueEvent("queue"))
        if was_current:
            self._emit(QueueEvent("current"))

    def play_next(self, index: int) -> None:
        """Move the item at index to just after the current track."""
        if self._index is None or index == self._index:
            return
        target = self._index + 1 if index > self._index else self._index
        self.move(index, min(target, len(self._tracks) - 1))

    # ------------------------------------------------------------------
    # shuffle / repeat
    # ------------------------------------------------------------------

    def set_shuffle(self, enabled: bool) -> None:
        if enabled == self._shuffle:
            return
        self._shuffle = enabled
        current = self.current_track
        if enabled:
            if current is not None:
                self._tracks = self._shuffled_from(self._tracks, current)
                self._index = 0
        else:
            present = {t.file_hash for t in self._tracks}
            self._tracks = [t for t in self._original if t.file_hash in present]
            self._index = self._tracks.index(current) if current in self._tracks else None
        self._emit(QueueEvent("queue"))
        self._emit(QueueEvent("current"))

    def cycle_repeat(self) -> str:
        i = REPEAT_MODES.index(self._repeat)
        self._repeat = REPEAT_MODES[(i + 1) % len(REPEAT_MODES)]
        return self._repeat

    def _shuffled_from(self, tracks: list[Track], first: Track) -> list[Track]:
        rest = [t for t in tracks if t is not first]
        self._rng.shuffle(rest)
        return [first, *rest]

    # ------------------------------------------------------------------
    # observation
    # ------------------------------------------------------------------

    def subscribe(self, callback: Callable[[QueueEvent], None]) -> None:
        self._subscribers.append(callback)

    def _emit(self, event: QueueEvent) -> None:
        for callback in self._subscribers:
            callback(event)
