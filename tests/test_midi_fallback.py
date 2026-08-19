"""Gate 4 / H1: a missing MIDI port must not stop the app from starting."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.application import Application
from guitar_helper.config import AppConfig
from guitar_helper.midi import mido_port as mido_port_module
from guitar_helper.midi.mido_port import DEFAULT_PORT_NAME, MidoPort
from guitar_helper.midi.mock_port import MockMidiPort
from guitar_helper.midi.null_port import NullMidiPort
from tests.conftest import make_segment


class _FakeMidoOutput:
    def __init__(self) -> None:
        self.messages = []
        self.closed = False

    def send(self, message) -> None:
        self.messages.append(message)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def no_ports(monkeypatch):
    """The machine running the suite may or may not have loopMIDI; never ask it."""
    monkeypatch.setattr(
        mido_port_module, "mido", SimpleNamespace(get_output_names=lambda: [])
    )


@pytest.fixture
def one_port(monkeypatch):
    opened = _FakeMidoOutput()
    monkeypatch.setattr(
        mido_port_module,
        "mido",
        SimpleNamespace(
            get_output_names=lambda: [DEFAULT_PORT_NAME],
            open_output=lambda name: opened,
            Message=lambda *a, **kw: SimpleNamespace(args=a, kwargs=kw),
        ),
    )
    return opened


def test_application_constructs_when_no_midi_port_exists(no_ports, store, tmp_path):
    application = Application(AppConfig.resolve(tmp_path), store=store)

    assert application.midi_available is False
    assert application.midi_error is not None
    assert DEFAULT_PORT_NAME in application.midi_error
    assert isinstance(application.port, NullMidiPort)


def test_null_port_sends_and_close_are_harmless(no_ports, store, tmp_path):
    application = Application(AppConfig.resolve(tmp_path), store=store)

    application.port.send_program_change(0, 4)
    application.port.close()
    application.shutdown()


def test_null_port_is_not_the_recording_test_double(no_ports, store, tmp_path):
    """A live session dispatches for hours; the fallback must not accumulate."""
    application = Application(AppConfig.resolve(tmp_path), store=store)

    assert not isinstance(application.port, MockMidiPort)
    assert not hasattr(application.port, "sent")


def test_injected_port_is_never_replaced_by_the_fallback(no_ports, store, tmp_path):
    port = MockMidiPort()
    application = Application(AppConfig.resolve(tmp_path), store=store, port=port)

    assert application.port is port
    assert application.midi_available is True
    assert application.midi_error is None


def test_available_port_is_opened_and_reported_available(one_port, store, tmp_path):
    application = Application(AppConfig.resolve(tmp_path), store=store)

    assert application.midi_available is True
    assert application.midi_error is None
    assert isinstance(application.port, MidoPort)


def test_playback_graph_dispatches_into_the_null_port_without_raising(
    no_ports, db, store, make_wav, tmp_path
):
    wav = make_wav(duration_s=2.0, sr=22050)
    file_hash = AudioLoader().hash_file(wav)
    db.execute(
        "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at) VALUES (?,?,?,?)",
        (file_hash, "test.wav", 2000, "2026-01-01T00:00:00+00:00"),
    )
    db.commit()
    store.save_segments(file_hash, [make_segment(file_hash, 0, 2000, "metal")])

    application = Application(AppConfig.resolve(tmp_path), store=store)
    application.load(wav)
    application.tracker.set_cursor(0)
    application.dispatcher.tick()

    assert application.dispatch_log.last_send is not None


def test_ui_run_does_not_abort_when_the_midi_port_is_missing(
    no_ports, monkeypatch, tmp_path
):
    pytest.importorskip("pytestqt")
    from PySide6.QtWidgets import QApplication

    from guitar_helper.ui import app as ui_app

    QApplication.instance() or QApplication([])

    built = []

    class _StubWindow:
        def __init__(self, application) -> None:
            built.append(application)

        def resize(self, *_a) -> None:
            pass

        def show(self) -> None:
            pass

    monkeypatch.setattr(ui_app, "MainWindow", _StubWindow)
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)

    assert ui_app.run(AppConfig.resolve(tmp_path)) == 0
    assert built and built[0].midi_available is False
