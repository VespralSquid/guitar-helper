from __future__ import annotations

import logging
import time

from guitar_helper.db.interfaces import Preset
from guitar_helper.midi.mock_port import MockMidiPort
from guitar_helper.playback.midi_dispatcher import MidiDispatcher
from guitar_helper.playback.position_tracker import PositionTracker
from guitar_helper.playback.segment_lookup import SegmentLookup
from tests.conftest import make_segment


def _setup(store, track_hash, segments, lookahead_ms=0):
    store.save_segments(track_hash, segments)
    tracker = PositionTracker(sr=1000)  # cursor == position_ms
    lookup = SegmentLookup(store, track_hash)
    port = MockMidiPort()
    dispatcher = MidiDispatcher(store, lookup, tracker, port, lookahead_ms=lookahead_ms)

    def tick_at(ms: int) -> None:
        tracker.set_cursor(ms)
        dispatcher.tick()

    return dispatcher, tracker, port, tick_at


def test_dispatch_only_on_tone_change(store, track_hash):
    _, _, port, tick_at = _setup(store, track_hash, [
        make_segment(track_hash, 0, 1000, "clean"),
        make_segment(track_hash, 1000, 2000, "metal"),
        make_segment(track_hash, 2000, 3000, "metal"),
        make_segment(track_hash, 3000, 4000, "crunch"),
    ])
    for ms in (0, 200, 500):   # still clean
        tick_at(ms)
    tick_at(1000)   # metal
    tick_at(1500)   # still metal
    tick_at(2500)   # still metal (adjacent segment, same tone)
    tick_at(3000)   # crunch

    assert port.sent == [(0, 0), (0, 2), (0, 1)]  # clean, metal, crunch


def test_other_holds_preset_no_dispatch_and_logs(store, track_hash, caplog):
    _, _, port, tick_at = _setup(store, track_hash, [
        make_segment(track_hash, 0, 1000, "clean"),
        make_segment(track_hash, 1000, 2000, "other"),
        make_segment(track_hash, 2000, 3000, "clean"),
    ])
    with caplog.at_level(logging.INFO):
        tick_at(0)      # clean -> PC0
        tick_at(1000)   # other -> hold
        tick_at(2000)   # back to clean, amp already PC0

    assert port.sent == [(0, 0)]  # only the initial clean dispatch
    assert any("other" in r.message.lower() for r in caplog.records)


def test_boundary_lookahead_fires_early(store, track_hash):
    _, _, port, tick_at = _setup(store, track_hash, [
        make_segment(track_hash, 0, 1000, "clean"),
        make_segment(track_hash, 1000, 2000, "metal"),
    ], lookahead_ms=75)

    tick_at(0)      # clean
    tick_at(924)    # effective 999 -> still clean
    assert port.sent == [(0, 0)]
    tick_at(925)    # effective 1000 -> metal, fires 75ms early
    assert port.sent == [(0, 0), (0, 2)]


def test_pc_sourced_from_presets_table(store, track_hash):
    store.save_preset(Preset("clean", "Clean", 7))  # remap clean off its default PC0
    _, _, port, tick_at = _setup(store, track_hash, [
        make_segment(track_hash, 0, 1000, "clean"),
    ])
    tick_at(0)
    assert port.sent == [(0, 7)]  # read from table, not hardcoded 0


def test_reset_reevaluates_without_redundant_resend(store, track_hash):
    dispatcher, _, port, tick_at = _setup(store, track_hash, [
        make_segment(track_hash, 0, 5000, "clean"),
    ])
    tick_at(0)
    tick_at(2000)   # same tone, no resend
    assert port.sent == [(0, 0)]
    dispatcher.reset()   # e.g. after a seek
    tick_at(2000)        # re-evaluates, amp already on PC0 -> no resend
    assert port.sent == [(0, 0)]


def test_dispatch_loop_runs_on_its_own_thread(store, track_hash):
    """Regression: the dispatch thread must not touch the SQLite connection
    (segments are snapshotted at construction on the calling thread)."""
    dispatcher, tracker, port, _ = _setup(store, track_hash, [
        make_segment(track_hash, 0, 5000, "metal"),
    ])
    dispatcher._poll_interval_s = 0.01
    tracker.set_cursor(1000)
    dispatcher.start()
    time.sleep(0.1)
    dispatcher.stop()
    assert (0, 2) in port.sent
