from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.ui.modes.home import HomeMode  # noqa: E402


def _make_home(qtbot, store) -> HomeMode:
    home = HomeMode(store)
    qtbot.addWidget(home)
    return home


def test_correct_labels_button_disabled_when_playlist_empty(qtbot, store):
    home = _make_home(qtbot, store)
    assert not home.correct_labels_button.isEnabled()  # virtual Library, no tracks yet


def test_correct_labels_button_enabled_once_tracks_exist(qtbot, store, track_hash):
    store.save_track(track_hash, "song.wav", "Song", "Artist", 60000)
    home = _make_home(qtbot, store)
    assert home.correct_labels_button.isEnabled()


def test_correct_labels_click_emits_library_name_and_first_track(qtbot, store, track_hash):
    store.save_track(track_hash, "song.wav", "Song", "Artist", 60000)
    home = _make_home(qtbot, store)

    with qtbot.waitSignal(home.correctLabelsRequested, timeout=1000) as blocker:
        home.correct_labels_button.click()

    name, tracks, start_index = blocker.args
    assert name == "Library"
    assert start_index == 0
    assert tracks[0].file_hash == track_hash


def test_correct_labels_click_uses_selected_row_as_start_index(qtbot, store, db):
    for h, fname in (("h1", "a.wav"), ("h2", "b.wav")):
        store.save_track(h, fname, None, None, 1000)
    home = _make_home(qtbot, store)

    home.song_table.selectRow(1)
    with qtbot.waitSignal(home.correctLabelsRequested, timeout=1000) as blocker:
        home.correct_labels_button.click()

    _, tracks, start_index = blocker.args
    assert start_index == 1
    assert tracks[1].file_hash == "h2"


def test_correct_labels_click_uses_real_playlist_name(qtbot, store, track_hash):
    store.save_track(track_hash, "song.wav", "Song", "Artist", 60000)
    playlist_id = store.create_playlist("Practice")
    store.add_to_playlist(playlist_id, track_hash)
    home = _make_home(qtbot, store)

    home.playlist_list.setCurrentRow(1)  # 0 = virtual Library, 1 = Practice
    with qtbot.waitSignal(home.correctLabelsRequested, timeout=1000) as blocker:
        home.correct_labels_button.click()

    name, _tracks, _start_index = blocker.args
    assert name == "Practice"


# ----------------------------------------------------------------------
# Add songs (ISSUE-007 ingestion entry point)
# ----------------------------------------------------------------------

def test_add_songs_is_reachable_on_an_empty_library(qtbot, store):
    """The check whose absence allowed ISSUE-007: a user with nothing in the
    library must still have a way in."""
    home = _make_home(qtbot, store)
    assert home.add_songs_button.isEnabled()
    assert not home.empty_library_label.isHidden()


def test_add_songs_emits_none_for_the_virtual_library(qtbot, store):
    home = _make_home(qtbot, store)
    with qtbot.waitSignal(home.addSongsRequested, timeout=1000) as blocker:
        home.add_songs_button.click()
    assert blocker.args == [None]


def test_add_songs_emits_the_selected_playlist_id(qtbot, store, track_hash):
    store.save_track(track_hash, "song.wav", "Song", "Artist", 60000)
    playlist_id = store.create_playlist("Practice")
    home = _make_home(qtbot, store)

    home.playlist_list.setCurrentRow(1)  # 0 = virtual Library
    with qtbot.waitSignal(home.addSongsRequested, timeout=1000) as blocker:
        home.add_songs_button.click()
    assert blocker.args == [playlist_id]


# ----------------------------------------------------------------------
# Remove from library
# ----------------------------------------------------------------------

def _stub_confirm(monkeypatch, answer):
    from PySide6.QtWidgets import QMessageBox
    seen: list[str] = []

    def _warning(_parent, _title, text, *_args, **_kwargs):
        seen.append(text)
        return answer

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_warning))
    return seen


def _seed_corrected_track(store, file_hash="abc123"):
    from tests.conftest import make_segment
    store.save_track(file_hash, "song.wav", "Song", "Artist", 60000)
    store.save_segments(file_hash, [
        make_segment(file_hash, 0, 1000, "clean", manually_corrected=True),
        make_segment(file_hash, 1000, 2000, "metal", manually_corrected=True),
        make_segment(file_hash, 2000, 3000, "clean"),
    ])
    return file_hash


def test_removal_confirmation_names_the_corrected_segment_count(qtbot, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    file_hash = _seed_corrected_track(store)
    seen = _stub_confirm(monkeypatch, QMessageBox.StandardButton.Yes)
    home = _make_home(qtbot, store)

    with qtbot.waitSignal(home.trackRemoved, timeout=1000) as blocker:
        assert home.remove_from_library(home.track_model.tracks[0])

    assert blocker.args == [file_hash]
    assert "2 corrected segments" in seen[0]
    assert store.get_segments(file_hash) == []


def test_removal_confirmation_is_singular_for_one_correction(qtbot, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from tests.conftest import make_segment
    store.save_track("h1", "a.wav", None, None, 1000)
    store.save_segments("h1", [make_segment("h1", 0, 1000, "clean", manually_corrected=True)])
    seen = _stub_confirm(monkeypatch, QMessageBox.StandardButton.Yes)
    home = _make_home(qtbot, store)

    home.remove_from_library(home.track_model.tracks[0])
    assert "1 corrected segment," in seen[0]


def test_removal_cancelled_keeps_the_track(qtbot, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    file_hash = _seed_corrected_track(store)
    _stub_confirm(monkeypatch, QMessageBox.StandardButton.Cancel)
    home = _make_home(qtbot, store)

    assert not home.remove_from_library(home.track_model.tracks[0])
    assert len(store.get_segments(file_hash)) == 3


def test_removal_of_a_playlist_member_succeeds(qtbot, store, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    file_hash = _seed_corrected_track(store)
    playlist_id = store.create_playlist("Practice")
    store.add_to_playlist(playlist_id, file_hash)
    _stub_confirm(monkeypatch, QMessageBox.StandardButton.Yes)
    home = _make_home(qtbot, store)

    assert home.remove_from_library(home.track_model.tracks[0])
    assert store.get_playlist_tracks(playlist_id) == []


# ----------------------------------------------------------------------
# H5 — playlist creation errors
# ----------------------------------------------------------------------

def test_duplicate_playlist_name_is_reported_as_such(qtbot, store, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    store.create_playlist("Practice")
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Practice", True)))
    seen = _stub_confirm(monkeypatch, QMessageBox.StandardButton.Ok)
    home = _make_home(qtbot, store)

    home.new_playlist_button.click()
    assert "already exists" in seen[0]


def test_unexpected_playlist_failure_is_not_reported_as_a_duplicate(qtbot, store, monkeypatch):
    """H5: a bare `except Exception` told the user a disk error was a name
    clash. Anything that is not an IntegrityError must surface."""
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("New", True)))
    home = _make_home(qtbot, store)

    def _boom(_name):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(store, "create_playlist", _boom)
    # Called directly: an exception escaping a Qt slot goes to the event loop,
    # where pytest.raises around click() cannot observe it.
    with pytest.raises(RuntimeError, match="disk on fire"):
        home._on_new_playlist()
