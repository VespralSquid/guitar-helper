from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.ui.modes.home import HomeMode  # noqa: E402


def _make_home(qtbot, store) -> HomeMode:
    home = HomeMode(store)
    qtbot.addWidget(home)
    return home


def test_analyze_button_disabled_when_playlist_empty(qtbot, store):
    home = _make_home(qtbot, store)
    assert not home.analyze_button.isEnabled()  # virtual Library, no tracks yet


def test_analyze_button_enabled_once_tracks_exist(qtbot, store, track_hash):
    store.save_track(track_hash, "song.wav", "Song", "Artist", 60000)
    home = _make_home(qtbot, store)
    assert home.analyze_button.isEnabled()


def test_analyze_click_emits_library_name_and_first_track(qtbot, store, track_hash):
    store.save_track(track_hash, "song.wav", "Song", "Artist", 60000)
    home = _make_home(qtbot, store)

    with qtbot.waitSignal(home.analyzeRequested, timeout=1000) as blocker:
        home.analyze_button.click()

    name, tracks, start_index = blocker.args
    assert name == "Library"
    assert start_index == 0
    assert tracks[0].file_hash == track_hash


def test_analyze_click_uses_selected_row_as_start_index(qtbot, store, db):
    for h, fname in (("h1", "a.wav"), ("h2", "b.wav")):
        store.save_track(h, fname, None, None, 1000)
    home = _make_home(qtbot, store)

    home.song_table.selectRow(1)
    with qtbot.waitSignal(home.analyzeRequested, timeout=1000) as blocker:
        home.analyze_button.click()

    _, tracks, start_index = blocker.args
    assert start_index == 1
    assert tracks[1].file_hash == "h2"


def test_analyze_click_uses_real_playlist_name(qtbot, store, track_hash):
    store.save_track(track_hash, "song.wav", "Song", "Artist", 60000)
    playlist_id = store.create_playlist("Practice")
    store.add_to_playlist(playlist_id, track_hash)
    home = _make_home(qtbot, store)

    home.playlist_list.setCurrentRow(1)  # 0 = virtual Library, 1 = Practice
    with qtbot.waitSignal(home.analyzeRequested, timeout=1000) as blocker:
        home.analyze_button.click()

    name, _tracks, _start_index = blocker.args
    assert name == "Practice"
