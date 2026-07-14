from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.analysis.audio_loader import AudioLoader  # noqa: E402
from guitar_helper.application import Application  # noqa: E402
from guitar_helper.config import AppConfig  # noqa: E402
from guitar_helper.midi.mock_port import MockMidiPort  # noqa: E402
from guitar_helper.ui.main_window import (  # noqa: E402
    MODE_ANALYSIS,
    MODE_HOME,
    MODE_OUTPUT,
    MainWindow,
)
from tests.conftest import make_segment  # noqa: E402


@pytest.fixture
def window(qtbot, db, store, make_wav, tmp_path, monkeypatch):
    hashes = []
    loader = AudioLoader()
    # Different durations -> different file content -> distinct hashes.
    for filename, duration_s in (("test.wav", 2.0), ("test2.wav", 1.5)):
        wav = make_wav(filename, duration_s=duration_s, sr=22050)
        file_hash = loader.hash_file(wav)
        hashes.append(file_hash)
        duration_ms = int(duration_s * 1000)
        db.execute(
            "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at, source_path) "
            "VALUES (?,?,?,?,?)",
            (file_hash, filename, duration_ms, "2026-01-01T00:00:00+00:00", str(wav)),
        )
        store.save_segments(
            file_hash,
            [
                make_segment(file_hash, 0, duration_ms // 2, "clean", manually_corrected=True),
                make_segment(file_hash, duration_ms // 2, duration_ms, "metal"),
            ],
        )
    db.commit()

    cfg = AppConfig.resolve(tmp_path)
    application = Application(cfg, store=store, port=MockMidiPort())
    play_calls: list[bool] = []
    monkeypatch.setattr(application, "play", lambda: play_calls.append(True))

    win = MainWindow(application)
    win.play_calls = play_calls
    qtbot.addWidget(win)
    yield win, hashes
    win.close()


def _play_row(win, qtbot, row: int = 0) -> None:
    index = win.home.track_model.index(row, 0)
    with qtbot.waitSignal(win.loadFinished, timeout=5000):
        win.home.song_table.doubleClicked.emit(index)


def test_main_window_builds_playlist_first_home(window):
    win, _ = window
    assert win.sidebar.currentRow() == MODE_HOME
    assert win._stack.currentWidget() is win.home
    assert win.home.playlist_list.count() == 1  # virtual Library only
    assert win.home.playlist_list.item(0).text().startswith("Library")
    assert win.home.track_model.rowCount() == 2
    assert win.home.hint_label.isVisibleTo(win.home)  # no playlists yet -> prompt


def test_double_click_plays_and_stays_in_home(window, qtbot):
    win, hashes = window
    _play_row(win, qtbot, 0)

    assert win._app.tracker is not None
    assert win.play_calls  # autoplay fired
    assert win._stack.currentWidget() is win.home  # no auto-jump to Analysis
    assert win._state.file_hash == hashes[0]


def test_load_seeds_queue_and_now_playing(window, qtbot):
    win, _ = window
    _play_row(win, qtbot, 0)

    assert win.queue_sidebar.queue_list.count() == 2  # whole Library queued
    assert win._queue.current_index == 0
    assert "test.wav" in win.queue_sidebar.now_playing_title.text()


def test_transport_next_advances_queue(window, qtbot):
    win, hashes = window
    _play_row(win, qtbot, 0)

    with qtbot.waitSignal(win.loadFinished, timeout=5000):
        win.transport.next_button.click()

    assert win._state.file_hash == hashes[1]
    assert win._queue.current_index == 1
    assert "test2.wav" in win.queue_sidebar.now_playing_title.text()


def test_playlist_crud_reflected_in_home(window, qtbot):
    win, hashes = window
    playlist_id = win._app.store.create_playlist("Riffs")
    win._app.store.add_to_playlist(playlist_id, hashes[1])
    win.home.refresh()

    assert win.home.playlist_list.count() == 2
    assert not win.home.hint_label.isVisibleTo(win.home)
    win.home.playlist_list.setCurrentRow(1)
    assert win.home.track_model.rowCount() == 1
    assert win.home.track_model.track_at_row(0).file_hash == hashes[1]


def test_analysis_populates_after_load(window, qtbot):
    win, _ = window
    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_ANALYSIS)

    assert win._stack.currentWidget() is win.analysis
    assert win.analysis.segment_model.rowCount() == 2


def test_mode_switching_keeps_transport_and_timer_alive(window, qtbot):
    win, _ = window
    _play_row(win, qtbot, 0)

    win.sidebar.setCurrentRow(MODE_OUTPUT)
    assert win._pos_timer.isActive()
    assert win.transport.play_button.isEnabled()
    win.sidebar.setCurrentRow(MODE_HOME)


def test_reload_replaces_runtime_without_orphans(window, qtbot):
    win, _ = window
    _play_row(win, qtbot, 0)
    first_engine = win._app.engine

    _play_row(win, qtbot, 1)

    assert win._app.engine is not first_engine
    assert first_engine._stream is None  # old engine torn down, not orphaned


def test_home_disabled_while_loading(window, qtbot):
    win, _ = window
    index = win.home.track_model.index(0, 0)
    with qtbot.waitSignal(win.loadFinished, timeout=5000):
        win.home.song_table.doubleClicked.emit(index)
        assert not win.home.isEnabled()
    assert win.home.isEnabled()


def test_timer_stops_on_close(window, qtbot):
    win, _ = window
    _play_row(win, qtbot, 0)
    assert win._pos_timer.isActive()
    win._on_pos_tick()  # must not touch the audio device
    win.close()
    assert not win._pos_timer.isActive()
