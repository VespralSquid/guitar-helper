from __future__ import annotations

import bisect

from guitar_helper.db.interfaces import ISegmentStore, Segment


class SegmentLookup:
    """Position→segment resolver over a snapshot of a track's segments.

    Segments are loaded once at construction (on the calling thread) and
    resolved in memory, so the MIDI/timing thread never touches the DB —
    SQLite connections are not shareable across threads, and a per-tick query
    has no place in the dispatch loop.
    """

    def __init__(self, store: ISegmentStore, file_hash: str) -> None:
        self._segments = store.get_segments(file_hash)  # ordered by start_ms
        self._starts = [s.start_ms for s in self._segments]

    def at(self, position_ms: int) -> Segment | None:
        i = bisect.bisect_right(self._starts, position_ms) - 1
        if i < 0:
            return None
        seg = self._segments[i]
        return seg if seg.start_ms <= position_ms < seg.end_ms else None

    def all(self) -> list[Segment]:
        return list(self._segments)
