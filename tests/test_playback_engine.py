from __future__ import annotations

import queue

import numpy as np

from guitar_helper.db.interfaces import ISegmentStore
from guitar_helper.midi.interfaces import IMidiPort
from guitar_helper.playback.audio_buffer import AudioBuffer
from guitar_helper.playback.playback_engine import PlaybackEngine
from guitar_helper.playback.position_tracker import PositionTracker


def _engine(sr=1000, channels=1):
    data = np.arange(sr * 2 * channels, dtype=np.float32).reshape(-1, channels)
    buffer = AudioBuffer(data, sr)
    tracker = PositionTracker(sr)
    q: queue.Queue = queue.Queue(maxsize=8)
    return PlaybackEngine(buffer, tracker, q), buffer, tracker, q


def test_callback_fills_output_and_advances_cursor():
    engine, buffer, tracker, q = _engine()
    frames = 100
    out = np.zeros((frames, 1), dtype=np.float32)
    engine._callback(out, frames, None, None)

    assert np.array_equal(out, buffer.data[0:frames])
    assert tracker.cursor == frames
    cursor, chunk = q.get_nowait()
    assert cursor == 0
    assert chunk.shape[0] == frames


def test_callback_zero_pads_at_end():
    engine, buffer, tracker, q = _engine()
    engine.seek(0)
    engine._frame = buffer.n_frames - 10
    out = np.ones((50, 1), dtype=np.float32)
    try:
        engine._callback(out, 50, None, None)
    except Exception:  # noqa: BLE001 - sd.CallbackStop expected at EOF
        pass
    assert np.array_equal(out[10:], np.zeros((40, 1), dtype=np.float32))


def test_seek_sets_cursor_and_position():
    engine, buffer, tracker, q = _engine(sr=1000)
    engine.seek(1500)
    assert tracker.position_ms == 1500
    assert tracker.cursor == 1500


def test_seek_clamps_to_bounds():
    engine, buffer, tracker, q = _engine(sr=1000)
    engine.seek(-500)
    assert tracker.cursor == 0
    engine.seek(999999)
    assert tracker.cursor == buffer.n_frames


def test_callback_is_isolated_from_db_and_midi():
    """Non-blocking invariant: the engine holds no store/port, so the audio
    callback structurally cannot make DB or MIDI calls."""
    engine, *_ = _engine()
    for value in vars(engine).values():
        assert not isinstance(value, (ISegmentStore, IMidiPort))
    assert not hasattr(engine, "store")
    assert not hasattr(engine, "port")
    assert not hasattr(engine, "dispatcher")
