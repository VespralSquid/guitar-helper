from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402

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
    wav_paths = []
    loader = AudioLoader()
    # Different durations -> different file content -> distinct hashes.
    for filename, duration_s in (("test.wav", 2.0), ("test2.wav", 1.5)):
        wav = make_wav(filename, duration_s=duration_s, sr=22050)
        wav_paths.append(wav)
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
    win.wav_paths = wav_paths
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


def test_relabel_save_round_trips_through_wiring_to_store(window, qtbot):
    """Drives the real signal chain: AnalysisMode -> MainWindow -> EditorState
    -> store, proving the step 7 wiring is actually connected end to end."""
    win, hashes = window
    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_ANALYSIS)

    win.analysis.segment_table.selectRow(0)
    assert win._state.selection_index == 0

    win.analysis.relabel_combo.textActivated.emit("edge")

    assert win._state.dirty is True
    assert win.analysis.segment_model.segment_at_row(0).tone_label == "edge"
    assert win.analysis.save_button.isEnabled()

    win.analysis.save_button.click()

    assert win._state.dirty is False
    assert win._app.store.get_segments(hashes[0])[0].tone_label == "edge"


def test_merge_moves_selection_and_shrinks_table(window, qtbot):
    win, hashes = window
    win._app.store.save_segments(
        hashes[0],
        [
            make_segment(hashes[0], 0, 1000, "metal"),
            make_segment(hashes[0], 1000, 2000, "metal"),
        ],
    )
    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_ANALYSIS)

    win.analysis.segment_table.selectRow(0)
    win.analysis.merge_button.click()

    assert win.analysis.segment_model.rowCount() == 1
    assert win._state.selection_index == 0
    assert win._state.dirty is True

    win.analysis.save_button.click()
    live = win._app.store.get_segments(hashes[0])
    original = win._app.store.get_calibration_segments(hashes[0])
    assert len(live) == 1
    assert len(original) == 2  # pre-merge snapshot preserved


def _analyze_row(win, qtbot, row: int = 0) -> None:
    win.home.song_table.selectRow(row)
    with qtbot.waitSignal(win.loadFinished, timeout=5000):
        win.home.analyze_button.click()


def test_analyze_button_enters_analysis_with_header_and_autoplay(window, qtbot):
    win, hashes = window
    _analyze_row(win, qtbot, 0)

    assert win._stack.currentWidget() is win.analysis
    assert win.play_calls  # autoplay, same pipeline as double-click
    assert "Library" in win.analysis.header_label.text()
    assert "1 of 2" in win.analysis.header_label.text()
    assert not win.analysis.prev_song_button.isEnabled()  # first song
    assert win.analysis.next_song_button.isEnabled()


def test_analysis_next_song_advances_and_updates_header(window, qtbot):
    win, hashes = window
    _analyze_row(win, qtbot, 0)

    with qtbot.waitSignal(win.loadFinished, timeout=5000):
        win.analysis.next_song_button.click()

    assert win._state.file_hash == hashes[1]
    assert "2 of 2" in win.analysis.header_label.text()
    assert win.analysis.prev_song_button.isEnabled()
    assert not win.analysis.next_song_button.isEnabled()  # last song


def test_non_playlist_load_clears_playlist_header(window, qtbot):
    win, hashes = window
    _analyze_row(win, qtbot, 0)
    assert win.analysis.header_label.text() != ""

    _play_row(win, qtbot, 1)  # ordinary Home double-click, not Analyze

    assert win.analysis.header_label.text() == ""
    assert not win.analysis.prev_song_button.isEnabled()
    assert not win.analysis.next_song_button.isEnabled()


def test_analysis_next_prompts_discard_when_dirty(window, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    win, hashes = window
    _analyze_row(win, qtbot, 0)
    win.analysis.segment_table.selectRow(0)
    win.analysis.relabel_combo.textActivated.emit("edge")
    assert win._state.dirty is True

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    win.analysis.next_song_button.click()

    assert win._state.file_hash == hashes[0]  # navigation was cancelled
    assert win._state.dirty is True  # edit preserved, not silently discarded

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard
    )
    with qtbot.waitSignal(win.loadFinished, timeout=5000):
        win.analysis.next_song_button.click()

    assert win._state.file_hash == hashes[1]  # navigation proceeded after confirming


def test_boundary_drag_round_trips_to_store(window, qtbot):
    win, hashes = window
    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_ANALYSIS)

    original = win._app.store.get_segments(hashes[0])
    left_id, right_id = original[0].id, original[1].id
    boundary_ms = original[0].end_ms

    win.analysis.timeline.boundaryEditRequested.emit(0, boundary_ms - 100)
    assert win._state.dirty is True
    win.analysis.save_button.click()

    updated = {s.id: s for s in win._app.store.get_segments(hashes[0])}
    assert updated[left_id].end_ms == boundary_ms - 100
    assert updated[right_id].start_ms == boundary_ms - 100


def test_exclude_checkbox_round_trips_to_store(window, qtbot):
    win, hashes = window
    _play_row(win, qtbot, 0)
    assert win._app.store.get_calibration_excluded(hashes[0]) is False

    win.analysis.exclude_checkbox.click()

    assert win._app.store.get_calibration_excluded(hashes[0]) is True  # eager, no save needed


def test_confirm_all_round_trips_to_store(window, qtbot):
    win, hashes = window
    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_ANALYSIS)

    win.analysis.confirm_all_button.click()
    win.analysis.save_button.click()

    segments = win._app.store.get_segments(hashes[0])
    assert all(s.manually_corrected for s in segments)
    assert all(s.confidence == 1.0 for s in segments)


def test_discard_reverts_working_list_and_never_writes_store(window, qtbot):
    win, hashes = window
    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_ANALYSIS)
    original_label = win._app.store.get_segments(hashes[0])[0].tone_label

    win.analysis.segment_table.selectRow(0)
    win.analysis.relabel_combo.textActivated.emit("edge")
    assert win._state.dirty is True

    win.analysis.discard_button.click()

    assert win._state.dirty is False
    assert win.analysis.segment_model.segment_at_row(0).tone_label == original_label
    assert win._app.store.get_segments(hashes[0])[0].tone_label == original_label


def test_edit_session_composes_relabel_boundary_confirm_and_merge(window, qtbot):
    """Chains four edit ops on one track before a single save, proving they
    compose correctly (indices/ids stay valid across the chain) and that the
    calibration snapshot still reflects the track's pristine pre-session state."""
    win, hashes = window
    win._app.store.save_segments(
        hashes[0],
        [
            make_segment(hashes[0], 0, 1000, "clean"),
            make_segment(hashes[0], 1000, 2000, "metal"),
        ],
    )
    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_ANALYSIS)

    win.analysis.segment_table.selectRow(0)
    win.analysis.relabel_combo.textActivated.emit("metal")  # now matches segment 1

    win.analysis.timeline.boundaryEditRequested.emit(0, 800)

    win.analysis.segment_table.selectRow(1)
    win.analysis.confirm_button.click()

    win.analysis.segment_table.selectRow(0)
    win.analysis.merge_button.click()

    assert win.analysis.segment_model.rowCount() == 1
    assert win._state.dirty is True

    win.analysis.save_button.click()

    live = win._app.store.get_segments(hashes[0])
    assert len(live) == 1
    assert live[0].tone_label == "metal"
    assert live[0].start_ms == 0
    assert live[0].end_ms == 2000

    original = win._app.store.get_calibration_segments(hashes[0])
    assert len(original) == 2
    assert [s.tone_label for s in original] == ["clean", "metal"]


def test_reanalysis_guard_fires_after_gui_edit_and_save(window, qtbot):
    """Closes the O3 acceptance loop: apply_* stamps manually_corrected=True,
    so a track that starts with NO corrections should become re-analysis
    -protected purely by being edited and saved through the GUI."""
    from guitar_helper.analysis.pipeline import AnalysisPipeline, ManualCorrectionsExistError

    win, hashes = window
    win._app.store.save_segments(
        hashes[0],
        [
            make_segment(hashes[0], 0, 1000, "clean", manually_corrected=False),
            make_segment(hashes[0], 1000, 2000, "metal", manually_corrected=False),
        ],
    )
    assert not any(s.manually_corrected for s in win._app.store.get_segments(hashes[0]))

    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_ANALYSIS)
    win.analysis.segment_table.selectRow(0)
    win.analysis.relabel_combo.textActivated.emit("edge")
    win.analysis.save_button.click()

    assert any(s.manually_corrected for s in win._app.store.get_segments(hashes[0]))

    from guitar_helper.analysis.source_separator import NullSeparator

    pipeline = AnalysisPipeline(win._app.store, separator=NullSeparator())
    with pytest.raises(ManualCorrectionsExistError):
        pipeline.run(win.wav_paths[0])


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


# ----------------------------------------------------------------------
# O4: Output mode wiring
# ----------------------------------------------------------------------

def test_dispatch_timer_runs_only_while_output_is_visible(window):
    """Same reasoning as the Analysis playhead skip: no periodic work for a
    view nobody is looking at."""
    win, _ = window
    assert not win._dispatch_timer.isActive()

    win.sidebar.setCurrentRow(MODE_OUTPUT)
    assert win._dispatch_timer.isActive()

    win.sidebar.setCurrentRow(MODE_HOME)
    assert not win._dispatch_timer.isActive()


def test_entering_output_shows_events_buffered_while_it_was_hidden(window, qtbot):
    """The log is a deque that keeps filling with Output closed, so opening it
    must not start from an empty screen."""
    win, _ = window
    _play_row(win, qtbot, 0)
    win._app.tracker.set_cursor(0)
    win._app.dispatcher.tick()

    win.sidebar.setCurrentRow(MODE_OUTPUT)

    assert win.output.log_list.count() >= 1
    assert "PC 0" in win.output.log_list.item(0).text()
    assert "clean" in win.output.active_label.text()


def test_preset_edit_reaches_the_live_dispatcher(window, qtbot):
    """End-to-end for the O4 promise: remap a PC in Output and the track that is
    already playing dispatches the new number without a reload."""
    win, _ = window
    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_OUTPUT)

    row = next(
        i for i, p in enumerate(win.output.preset_model.presets) if p.tone_label == "metal"
    )
    index = win.output.preset_model.index(row, 2)
    win.output.preset_model.setData(index, "11", Qt.ItemDataRole.EditRole)

    # set_cursor takes frames; land in the second half, which is the metal segment.
    win._app.tracker.set_cursor(int(win._app.buffer.sr * 1.1))
    win._app.dispatcher.tick()

    assert (0, 11) in win._app.port.sent


def test_offset_change_persists_and_reaches_the_dispatcher(window, qtbot):
    win, _ = window
    _play_row(win, qtbot, 0)
    win.sidebar.setCurrentRow(MODE_OUTPUT)

    win.output.offset_spin.setValue(22)
    win.output.apply_offset_button.click()

    assert win._app.dispatcher.lookahead_ms == 22
    assert win._app.store.get_int_setting("dispatch_offset_ms", 75) == 22


def test_output_seeds_its_spinbox_from_the_persisted_offset(window, qtbot, tmp_path):
    win, _ = window
    win._app.store.set_setting("dispatch_offset_ms", "31")

    cfg = AppConfig.resolve(tmp_path)
    application = Application(cfg, store=win._app.store, port=MockMidiPort())
    restarted = MainWindow(application)
    qtbot.addWidget(restarted)

    assert restarted.output.offset_spin.value() == 31
    restarted.close()


def test_test_send_button_reaches_the_midi_port(window, qtbot):
    """The ISSUE-004 diagnostic: prove the chain responds with nothing playing."""
    win, _ = window
    win.sidebar.setCurrentRow(MODE_OUTPUT)
    row = next(
        i for i, p in enumerate(win.output.preset_model.presets) if p.tone_label == "metal"
    )
    win.output.preset_table.selectRow(row)
    win.output.test_send_button.click()

    assert win._app.port.sent == [(0, 4)]
