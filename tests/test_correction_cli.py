"""Gate 4 / H3: the CLI save path must snapshot for calibration, like EditorState.save."""
from __future__ import annotations

import pytest

from guitar_helper.correction.cli import SegmentCorrectionTool
from tests.conftest import make_segment


@pytest.fixture
def seeded(store, track_hash):
    store.save_segments(track_hash, [
        make_segment(track_hash, 0, 1000, "clean"),
        make_segment(track_hash, 1000, 2000, "edge"),
    ])
    return store, track_hash


def _drive(monkeypatch, tool: SegmentCorrectionTool, file_hash: str, commands: list[str]) -> None:
    replies = iter(commands)
    monkeypatch.setattr("builtins.input", lambda *_a: next(replies))
    tool.run(file_hash)


def test_save_creates_the_calibration_snapshot(seeded, monkeypatch, capsys):
    store, file_hash = seeded
    assert store.get_calibration_segments(file_hash) == []

    _drive(monkeypatch, SegmentCorrectionTool(store), file_hash, ["0 metal", "s"])
    capsys.readouterr()

    snapshot = store.get_calibration_segments(file_hash)
    assert [s.tone_label for s in snapshot] == ["clean", "edge"]
    assert [s.tone_label for s in store.get_segments(file_hash)] == ["metal", "edge"]


def test_save_persists_the_pending_edits(seeded, monkeypatch, capsys):
    store, file_hash = seeded

    _drive(monkeypatch, SegmentCorrectionTool(store), file_hash, ["0 metal", "1 crunch", "s"])
    capsys.readouterr()

    saved = store.get_segments(file_hash)
    assert [s.tone_label for s in saved] == ["metal", "crunch"]
    assert all(s.manually_corrected for s in saved)


def test_second_save_does_not_overwrite_an_existing_snapshot(seeded, monkeypatch, capsys):
    store, file_hash = seeded

    _drive(monkeypatch, SegmentCorrectionTool(store), file_hash, ["0 metal", "s"])
    _drive(monkeypatch, SegmentCorrectionTool(store), file_hash, ["1 crunch", "s"])
    capsys.readouterr()

    snapshot = store.get_calibration_segments(file_hash)
    assert [s.tone_label for s in snapshot] == ["clean", "edge"]
    assert [s.tone_label for s in store.get_segments(file_hash)] == ["metal", "crunch"]


def test_quit_writes_nothing(seeded, monkeypatch, capsys):
    store, file_hash = seeded

    _drive(monkeypatch, SegmentCorrectionTool(store), file_hash, ["0 metal", "q"])
    capsys.readouterr()

    assert store.get_calibration_segments(file_hash) == []
    assert [s.tone_label for s in store.get_segments(file_hash)] == ["clean", "edge"]


def test_save_with_no_pending_edits_makes_no_snapshot(seeded, monkeypatch, capsys):
    store, file_hash = seeded

    _drive(monkeypatch, SegmentCorrectionTool(store), file_hash, ["s"])
    capsys.readouterr()

    assert store.get_calibration_segments(file_hash) == []
