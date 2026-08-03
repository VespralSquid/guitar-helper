from __future__ import annotations

import pytest

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.application import Application, NoSegmentsError
from guitar_helper.config import AppConfig
from guitar_helper.db.interfaces import Preset
from guitar_helper.midi.mock_port import MockMidiPort
from guitar_helper.playback.midi_dispatcher import DEFAULT_LOOKAHEAD_MS
from tests.conftest import make_segment


@pytest.fixture
def app(db, store, make_wav, tmp_path):
    wav = make_wav(duration_s=2.0, sr=22050)
    file_hash = AudioLoader().hash_file(wav)
    db.execute(
        "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at) VALUES (?,?,?,?)",
        (file_hash, "test.wav", 2000, "2026-01-01T00:00:00+00:00"),
    )
    db.commit()
    cfg = AppConfig.resolve(tmp_path)
    application = Application(cfg, store=store, port=MockMidiPort())
    return application, wav, file_hash


def test_load_builds_runtime_graph(app):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "metal")])

    returned = application.load(wav)
    assert returned == file_hash
    assert application.engine is not None
    assert application.dispatcher is not None
    assert application.tracker is not None
    assert abs(application.buffer.duration_ms - 2000) <= 5


def test_load_without_segments_raises(app):
    application, wav, _ = app
    with pytest.raises(NoSegmentsError):
        application.load(wav)


def test_wired_dispatch_uses_injected_port(app):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [
        make_segment(file_hash, 0, 1000, "clean"),
        make_segment(file_hash, 1000, 2000, "metal"),
    ])
    application.load(wav)

    application.tracker.set_cursor(0)
    application.dispatcher.tick()
    application.tracker.set_cursor(int(1.5 * application.buffer.sr))
    application.dispatcher.tick()

    assert application.port.sent == [(0, 0), (0, 4)]


def test_decode_returns_hash_and_buffer(app):
    application, wav, file_hash = app
    decoded_hash, buffer = application.decode(wav)
    assert decoded_hash == file_hash
    assert abs(buffer.duration_ms - 2000) <= 5
    assert application.engine is None  # decode alone must not touch runtime state


def test_reload_stops_previous_engine_and_dispatcher(app, monkeypatch):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "clean")])
    application.load(wav)

    old_engine, old_dispatcher = application.engine, application.dispatcher
    stopped = []
    monkeypatch.setattr(old_engine, "stop", lambda: stopped.append("engine"))
    monkeypatch.setattr(old_dispatcher, "stop", lambda: stopped.append("dispatcher"))

    application.load(wav)

    assert "engine" in stopped and "dispatcher" in stopped
    assert application.engine is not old_engine
    assert application.dispatcher is not old_dispatcher


def test_attach_unknown_hash_raises_and_preserves_runtime(app):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "clean")])
    application.load(wav)
    engine = application.engine

    _, buffer = application.decode(wav)
    with pytest.raises(NoSegmentsError):
        application.attach("deadbeef" * 8, buffer)

    assert application.engine is engine  # failed load leaves the current track intact


def test_play_pause_seek_are_noops_before_load(app):
    application, _, _ = app
    application.play()
    application.pause()
    application.seek(500)
    assert application.engine is None


def test_shutdown_closes_port(app):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "clean")])
    application.load(wav)
    application.shutdown()
    assert application.port.closed


# ----------------------------------------------------------------------
# O4: persisted dispatch offset, live preset refresh, dispatch log
# ----------------------------------------------------------------------

def test_dispatch_offset_defaults_to_dispatcher_default(app):
    application, _, _ = app
    assert application.dispatch_offset_ms == DEFAULT_LOOKAHEAD_MS


def test_dispatch_offset_persists_across_application_instances(app, tmp_path):
    """Calibration is a property of the rig, not the session — a restart must
    not silently return to the default."""
    application, _, _ = app
    application.set_dispatch_offset_ms(18)

    restarted = Application(
        AppConfig.resolve(tmp_path), store=application.store, port=MockMidiPort()
    )
    assert restarted.dispatch_offset_ms == 18


def test_attached_dispatcher_uses_persisted_offset(app):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "metal")])
    application.set_dispatch_offset_ms(25)

    application.load(wav)
    assert application.dispatcher.lookahead_ms == 25


def test_set_offset_applies_to_running_dispatcher(app):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "metal")])
    application.load(wav)

    application.set_dispatch_offset_ms(-30)
    assert application.dispatcher.lookahead_ms == -30


def test_reload_presets_updates_live_dispatcher(app):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "metal")])
    application.load(wav)
    application.store.save_preset(Preset("metal", "Metal", 9))

    application.reload_presets()
    application.tracker.set_cursor(0)
    application.dispatcher.tick()

    assert application.port.sent == [(0, 9)]


def test_reload_presets_is_safe_before_a_track_is_loaded(app):
    application, _, _ = app
    application.reload_presets()  # must not raise


def test_dispatch_log_collects_events_from_the_wired_dispatcher(app):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "metal")])
    application.load(wav)
    application.tracker.set_cursor(0)
    application.dispatcher.tick()

    events = application.dispatch_log.drain()
    assert [(e.kind, e.tone, e.pc) for e in events] == [("send", "metal", 4)]


def test_dispatch_log_survives_a_track_change(app, make_wav):
    """The Output panel keeps its history across loads — the log outlives the
    per-track dispatcher."""
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "metal")])
    application.load(wav)
    application.tracker.set_cursor(0)
    application.dispatcher.tick()

    application.load(wav)  # rebuilds the runtime graph
    assert application.dispatch_log.last_send is not None
    assert len(application.dispatch_log.drain()) == 1
