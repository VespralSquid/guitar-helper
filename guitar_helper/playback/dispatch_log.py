"""Ring buffer of MIDI dispatch decisions — the ISSUE-004 diagnostic view.

Qt-free and thread-safe: the dispatcher thread appends, the Qt main thread
drains on a timer. Every decision is recorded, not just the sends, so a track
that switches nothing is distinguishable from one whose tones all map to
'other' (hold) or to a missing preset row.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

MAX_EVENTS = 200

SEND = "send"          # Program Change actually written to the port
HOLD = "hold"          # 'other' tone: keep the current amp preset, send nothing
UNMAPPED = "unmapped"  # tone has no presets row — a data problem worth seeing
GAP = "gap"            # position fell between segments


@dataclass(frozen=True)
class DispatchEvent:
    position_ms: int
    kind: str
    tone: str | None
    pc: int | None = None


class DispatchLogBuffer:
    """Bounded, lock-guarded event log. `record` is the dispatcher's log_sink."""

    def __init__(self, maxlen: int = MAX_EVENTS) -> None:
        self._events: deque[DispatchEvent] = deque(maxlen=maxlen)
        self._last_send: DispatchEvent | None = None
        self._lock = threading.Lock()

    def record(self, event: DispatchEvent) -> None:
        with self._lock:
            self._events.append(event)
            if event.kind == SEND:
                self._last_send = event

    def drain(self) -> list[DispatchEvent]:
        """Return and remove everything buffered since the last drain."""
        with self._lock:
            drained = list(self._events)
            self._events.clear()
            return drained

    @property
    def last_send(self) -> DispatchEvent | None:
        """The most recent Program Change, surviving drains so the active-preset
        indicator is still right for a viewer who arrives after the switch."""
        with self._lock:
            return self._last_send

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._last_send = None
