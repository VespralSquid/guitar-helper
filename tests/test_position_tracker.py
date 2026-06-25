from __future__ import annotations

from guitar_helper.playback.position_tracker import PositionTracker
from tests.conftest import make_segment


def test_position_ms_math():
    tracker = PositionTracker(sr=22050)
    tracker.set_cursor(22050)
    assert tracker.position_ms == 1000
    tracker.set_cursor(11025)
    assert tracker.position_ms == 500
    tracker.set_cursor(0)
    assert tracker.position_ms == 0


def test_cursor_roundtrips():
    tracker = PositionTracker(sr=44100)
    tracker.set_cursor(4410)
    assert tracker.cursor == 4410
    assert tracker.position_ms == 100


def test_ms_until_next_boundary():
    segments = [
        make_segment("h", 0, 1000),
        make_segment("h", 1000, 2000),
        make_segment("h", 2000, 3000),
    ]
    assert PositionTracker.ms_until_next_boundary(500, segments) == 500
    assert PositionTracker.ms_until_next_boundary(1000, segments) == 1000
    assert PositionTracker.ms_until_next_boundary(1999, segments) == 1
    assert PositionTracker.ms_until_next_boundary(2500, segments) is None
