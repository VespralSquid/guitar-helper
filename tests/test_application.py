from __future__ import annotations

import pytest

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.application import Application, NoSegmentsError
from guitar_helper.config import AppConfig
from guitar_helper.midi.mock_port import MockMidiPort
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

    assert application.port.sent == [(0, 0), (0, 2)]


def test_shutdown_closes_port(app):
    application, wav, file_hash = app
    application.store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "clean")])
    application.load(wav)
    application.shutdown()
    assert application.port.closed
