from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.analysis.environment import (  # noqa: E402
    Check,
    EnvironmentReport,
    FileCheck,
    Severity,
)
from guitar_helper.config import AppConfig  # noqa: E402
from guitar_helper.db.interfaces import Playlist  # noqa: E402
from guitar_helper.ui.dialogs import add_songs as add_songs_module  # noqa: E402
from guitar_helper.ui.dialogs.add_songs import (  # noqa: E402
    PAUSE_PLAYBACK_SETTING,
    AddSongsDialog,
    expand_audio_files,
)


class FakeStore:
    def __init__(self, playlists=(), settings=None) -> None:
        self._playlists = list(playlists)
        self.settings = dict(settings or {})

    def list_playlists(self):
        return list(self._playlists)

    def get_setting(self, key):
        return self.settings.get(key)

    def set_setting(self, key, value):
        self.settings[key] = value


def _ok_check() -> Check:
    return Check(name="separation_stack", ok=True, severity=Severity.BLOCK, detail="", remedy="")


def _blocker() -> Check:
    return Check(
        name="separation_stack", ok=False, severity=Severity.BLOCK,
        detail="Missing module(s): torch", remedy="Install the separation stack.",
    )


def _report(paths=(), *, checks=None, rejected=()) -> EnvironmentReport:
    files = [FileCheck(path=Path(p), code="ok", detail="") for p in paths]
    files += list(rejected)
    return EnvironmentReport(checks=list(checks or [_ok_check()]), files=files)


@pytest.fixture
def stub_preflight(monkeypatch):
    """Replaces the real preflight so tests never depend on whether this
    machine has torch, ffmpeg or the htdemucs weights installed."""
    state = {"report": _report(), "calls": []}

    def _preflight(paths=(), **kwargs):
        state["calls"].append([Path(p) for p in paths])
        builder = state["report"]
        return builder(paths) if callable(builder) else builder

    monkeypatch.setattr(add_songs_module, "preflight", _preflight)
    return state


def _dialog(qtbot, tmp_path, store=None, **kwargs) -> AddSongsDialog:
    config = AppConfig.resolve(tmp_path)
    dialog = AddSongsDialog(store or FakeStore(), config, **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def _audio(tmp_path, *names: str) -> list[Path]:
    out = []
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        out.append(path)
    return out


# ---------------------------------------------------------------------------
# folder expansion
# ---------------------------------------------------------------------------

def test_expand_audio_files_is_top_level_only_by_default(tmp_path):
    _audio(tmp_path, "a.wav", "sub/b.wav")

    found = expand_audio_files([tmp_path], recursive=False)

    assert [p.name for p in found] == ["a.wav"]


def test_expand_audio_files_descends_when_recursive(tmp_path):
    _audio(tmp_path, "a.wav", "sub/b.wav")

    found = expand_audio_files([tmp_path], recursive=True)

    assert sorted(p.name for p in found) == ["a.wav", "b.wav"]


def test_expand_audio_files_ignores_unsupported_suffixes_inside_folders(tmp_path):
    _audio(tmp_path, "a.wav", "notes.txt")

    found = expand_audio_files([tmp_path], recursive=False)

    assert [p.name for p in found] == ["a.wav"]


def test_expand_audio_files_keeps_explicit_files_and_deduplicates(tmp_path):
    files = _audio(tmp_path, "a.wav")

    found = expand_audio_files([files[0], tmp_path, files[0]], recursive=False)

    assert found == files


# ---------------------------------------------------------------------------
# preflight gating
# ---------------------------------------------------------------------------

def test_ok_disabled_with_nothing_selected(qtbot, tmp_path, stub_preflight):
    dialog = _dialog(qtbot, tmp_path)

    assert not dialog.ok_button.isEnabled()


def test_ok_enabled_when_files_are_usable(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files)
    dialog = _dialog(qtbot, tmp_path)

    dialog.add_paths(files)

    assert dialog.ok_button.isEnabled()


def test_a_blocker_keeps_ok_disabled_even_with_usable_files(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files, checks=[_blocker()])
    dialog = _dialog(qtbot, tmp_path)

    dialog.add_paths(files)

    assert not dialog.ok_button.isEnabled()
    assert any("torch" in dialog.rejected_list.item(i).text()
               for i in range(dialog.rejected_list.count()))


def test_no_usable_files_keeps_ok_disabled(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.mp3")
    rejected = [FileCheck(path=files[0], code="needs_ffmpeg", detail="Requires ffmpeg, not found on PATH.")]
    stub_preflight["report"] = _report(rejected=rejected)
    dialog = _dialog(qtbot, tmp_path)

    dialog.add_paths(files)

    assert not dialog.ok_button.isEnabled()


def test_rejected_files_are_listed_with_their_detail(qtbot, tmp_path, stub_preflight):
    good, bad = _audio(tmp_path, "a.wav", "b.mp3")
    rejected = [FileCheck(path=bad, code="needs_ffmpeg", detail="Requires ffmpeg, not found on PATH.")]
    stub_preflight["report"] = _report([good], rejected=rejected)
    dialog = _dialog(qtbot, tmp_path)

    dialog.add_paths([good, bad])

    lines = [dialog.rejected_list.item(i).text() for i in range(dialog.rejected_list.count())]
    assert "b.mp3 - Requires ffmpeg, not found on PATH." in lines


def test_dialog_expands_folders_before_preflighting(qtbot, tmp_path, stub_preflight):
    _audio(tmp_path, "songs/a.wav", "songs/sub/b.wav")
    folder = tmp_path / "songs"
    stub_preflight["report"] = lambda paths: _report(paths)
    dialog = _dialog(qtbot, tmp_path)

    dialog.add_paths([folder])
    top_level = stub_preflight["calls"][-1]
    dialog.recursive_check.setChecked(True)
    recursive = stub_preflight["calls"][-1]

    assert [p.name for p in top_level] == ["a.wav"]
    assert sorted(p.name for p in recursive) == ["a.wav", "b.wav"]
    assert dialog.selected_paths() == (folder,)


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------

def test_options_is_none_before_accept(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files)
    dialog = _dialog(qtbot, tmp_path)
    dialog.add_paths(files)

    assert dialog.options() is None


def test_options_is_none_after_reject(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files)
    dialog = _dialog(qtbot, tmp_path)
    dialog.add_paths(files)

    dialog.reject()

    assert dialog.options() is None


def test_accept_returns_only_the_usable_files_flattened(qtbot, tmp_path, stub_preflight):
    good, bad = _audio(tmp_path, "songs/a.wav", "songs/b.mp3")
    rejected = [FileCheck(path=bad, code="needs_ffmpeg", detail="Requires ffmpeg.")]
    stub_preflight["report"] = _report([good], rejected=rejected)
    dialog = _dialog(qtbot, tmp_path)
    dialog.add_paths([tmp_path / "songs"])

    dialog.accept()

    assert dialog.options().paths == (good,)


def test_accept_carries_the_reanalyse_flag(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files)
    dialog = _dialog(qtbot, tmp_path)
    dialog.add_paths(files)
    dialog.reanalyse_check.setChecked(True)

    dialog.accept()

    assert dialog.options().reanalyse_existing is True


def test_accept_is_a_no_op_while_a_blocker_stands(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files, checks=[_blocker()])
    dialog = _dialog(qtbot, tmp_path)
    dialog.add_paths(files)

    dialog.accept()

    assert dialog.options() is None
    assert dialog.result() != int(dialog.DialogCode.Accepted)


# ---------------------------------------------------------------------------
# target playlist
# ---------------------------------------------------------------------------

def test_playlist_defaults_to_none(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files)
    store = FakeStore(playlists=[Playlist(id=7, name="Set 1", created_at="", track_count=0)])
    dialog = _dialog(qtbot, tmp_path, store)
    dialog.add_paths(files)

    dialog.accept()

    assert dialog.options().target_playlist_id is None


def test_preselected_playlist_is_selected(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files)
    store = FakeStore(playlists=[
        Playlist(id=7, name="Set 1", created_at="", track_count=0),
        Playlist(id=9, name="Set 2", created_at="", track_count=0),
    ])
    dialog = _dialog(qtbot, tmp_path, store, preselected_playlist_id=9)
    dialog.add_paths(files)

    dialog.accept()

    assert dialog.options().target_playlist_id == 9


# ---------------------------------------------------------------------------
# D3 setting
# ---------------------------------------------------------------------------

def test_pause_playback_defaults_to_unchecked(qtbot, tmp_path, stub_preflight):
    dialog = _dialog(qtbot, tmp_path, FakeStore())

    assert dialog.pause_check.isChecked() is False


def test_pause_playback_seeds_from_the_stored_setting(qtbot, tmp_path, stub_preflight):
    store = FakeStore(settings={PAUSE_PLAYBACK_SETTING: "1"})

    dialog = _dialog(qtbot, tmp_path, store)

    assert dialog.pause_check.isChecked() is True


def test_pause_playback_is_written_back_on_accept(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files)
    store = FakeStore()
    dialog = _dialog(qtbot, tmp_path, store)
    dialog.add_paths(files)
    dialog.pause_check.setChecked(True)

    dialog.accept()

    assert store.settings[PAUSE_PLAYBACK_SETTING] == "1"
    assert dialog.options().pause_playback is True


def test_unchecking_pause_playback_writes_zero(qtbot, tmp_path, stub_preflight):
    files = _audio(tmp_path, "a.wav")
    stub_preflight["report"] = _report(files)
    store = FakeStore(settings={PAUSE_PLAYBACK_SETTING: "1"})
    dialog = _dialog(qtbot, tmp_path, store)
    dialog.add_paths(files)
    dialog.pause_check.setChecked(False)

    dialog.accept()

    assert store.settings[PAUSE_PLAYBACK_SETTING] == "0"
