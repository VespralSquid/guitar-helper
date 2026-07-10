from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.analysis.audio_loader import AudioLoader  # noqa: E402
from guitar_helper.application import Application  # noqa: E402
from guitar_helper.config import AppConfig  # noqa: E402
from guitar_helper.midi.mock_port import MockMidiPort  # noqa: E402
from guitar_helper.ui.main_window import MainWindow  # noqa: E402
from tests.conftest import make_segment  # noqa: E402


@pytest.fixture
def window(qtbot, db, store, make_wav, tmp_path):
    wav = make_wav(duration_s=2.0, sr=22050)
    file_hash = AudioLoader().hash_file(wav)
    db.execute(
        "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at, source_path) "
        "VALUES (?,?,?,?,?)",
        (file_hash, "test.wav", 2000, "2026-01-01T00:00:00+00:00", str(wav)),
    )
    db.commit()
    store.save_segments(
        file_hash,
        [
            make_segment(file_hash, 0, 1000, "clean", manually_corrected=True),
            make_segment(file_hash, 1000, 2000, "metal"),
        ],
    )

    cfg = AppConfig.resolve(tmp_path)
    application = Application(cfg, store=store, port=MockMidiPort())
    win = MainWindow(application)
    qtbot.addWidget(win)
    yield win, file_hash
    win.close()


def test_main_window_builds(window):
    win, _ = window
    assert win.windowTitle()
    assert win.library_panel.list_widget.count() == 1


def test_selecting_library_row_loads_track_and_populates_table(window, qtbot):
    win, file_hash = window
    item = win.library_panel.list_widget.item(0)

    win.library_panel.list_widget.itemDoubleClicked.emit(item)

    assert win._app.tracker is not None
    assert win.segment_model.rowCount() == 2
    assert win._state.file_hash == file_hash


def test_selecting_table_row_updates_selection_state(window):
    win, _ = window
    item = win.library_panel.list_widget.item(0)
    win.library_panel.list_widget.itemDoubleClicked.emit(item)

    win.segment_table.selectRow(1)

    assert win._state.selection_index == 1


def test_timer_starts_and_stops_without_real_audio(window):
    win, _ = window
    item = win.library_panel.list_widget.item(0)
    win.library_panel.list_widget.itemDoubleClicked.emit(item)

    assert win._pos_timer.isActive()
    win._on_pos_tick()  # must not touch the audio device
    win.close()
    assert not win._pos_timer.isActive()
