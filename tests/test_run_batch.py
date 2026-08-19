from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import soundfile as sf

from guitar_helper.analysis.environment import SUPPORTED_SUFFIXES
from guitar_helper.db.interfaces import Segment
from guitar_helper.run_batch import _AUDIO_EXTENSIONS, _discover_audio, _read_tags, _tone_summary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_wav(path: Path, duration_s: float = 1.0, sr: int = 22050) -> Path:
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    y = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(str(path), y, sr)
    return path


def _make_segment(file_hash: str, tone: str = "crunch") -> Segment:
    return Segment(id=1, file_hash=file_hash, start_ms=0, end_ms=5000,
                   tone_label=tone, confidence=0.8, manually_corrected=False)


# ---------------------------------------------------------------------------
# _discover_audio
# ---------------------------------------------------------------------------

def test_discover_audio_top_level(tmp_path):
    _write_wav(tmp_path / "a.wav")
    _write_wav(tmp_path / "b.flac")
    sub = tmp_path / "sub"
    sub.mkdir()
    _write_wav(sub / "c.wav")

    found = _discover_audio(tmp_path, recursive=False)
    names = {p.name for p in found}
    assert "a.wav" in names
    assert "b.flac" in names
    assert "c.wav" not in names  # subdirectory excluded


def test_discover_audio_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    _write_wav(tmp_path / "a.wav")
    _write_wav(sub / "b.wav")

    found = _discover_audio(tmp_path, recursive=True)
    names = {p.name for p in found}
    assert "a.wav" in names
    assert "b.wav" in names


def test_discover_audio_ignores_non_audio(tmp_path):
    _write_wav(tmp_path / "song.wav")
    (tmp_path / "notes.txt").write_text("notes")
    (tmp_path / "cover.jpg").write_bytes(b"\xff\xd8")

    found = _discover_audio(tmp_path, recursive=False)
    assert all(p.suffix.lower() in _AUDIO_EXTENSIONS for p in found)
    assert len(found) == 1


def test_discover_audio_empty_folder(tmp_path):
    assert _discover_audio(tmp_path, recursive=False) == []


# ---------------------------------------------------------------------------
# _read_tags
# ---------------------------------------------------------------------------

def test_read_tags_fallback_on_missing_tags(tmp_path):
    path = _write_wav(tmp_path / "my_song.wav")
    title, artist = _read_tags(path)
    # WAV files written by soundfile carry no ID3 tags → fallback to stem
    assert title == "my_song"
    assert artist is None


def test_read_tags_fallback_on_exception(tmp_path):
    path = tmp_path / "ghost.wav"
    path.write_bytes(b"not audio")
    title, artist = _read_tags(path)
    assert title == "ghost"
    assert artist is None


# ---------------------------------------------------------------------------
# _tone_summary
# ---------------------------------------------------------------------------

def test_tone_summary_counts_labels():
    segs = [
        _make_segment("h", "edge"),
        _make_segment("h", "crunch"),
        _make_segment("h", "crunch"),
    ]
    summary = _tone_summary(segs)
    assert "crunch(2)" in summary
    assert "edge(1)" in summary


def test_tone_summary_empty():
    assert _tone_summary([]) == ""


# ---------------------------------------------------------------------------
# Skip logic — already-analyzed songs are not re-processed
# ---------------------------------------------------------------------------

def test_batch_skips_already_analyzed(tmp_path, capsys):
    _write_wav(tmp_path / "song.wav")

    mock_store = MagicMock()
    mock_store.get_segments.return_value = [_make_segment("fakehash")]

    mock_pipeline = MagicMock()

    with (
        patch("guitar_helper.run_batch.AudioLoader") as MockLoader,
        patch("guitar_helper.run_batch.SQLiteSegmentStore", return_value=mock_store),
        patch("guitar_helper.run_batch.init_db"),
        patch("guitar_helper.run_batch.AnalysisPipeline", return_value=mock_pipeline) as MockPipeline,
        patch("guitar_helper.run_batch.AudioSeparator") as MockSeparator,
    ):
        MockLoader.return_value.load.return_value = (5000, "fakehash")
        separator_instance = MockSeparator.return_value

        from guitar_helper.run_batch import main
        sys.argv = ["run_batch", str(tmp_path)]
        main()

    mock_pipeline.run.assert_not_called()
    assert MockPipeline.call_args.kwargs["separator"] is separator_instance
    captured = capsys.readouterr()
    assert "SKIP" in captured.out


def test_batch_reanalyze_flag_bypasses_skip(tmp_path, capsys):
    _write_wav(tmp_path / "song.wav")

    mock_store = MagicMock()
    mock_store.get_segments.return_value = [_make_segment("fakehash")]

    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = [_make_segment("fakehash")]

    with (
        patch("guitar_helper.run_batch.AudioLoader") as MockLoader,
        patch("guitar_helper.run_batch.SQLiteSegmentStore", return_value=mock_store),
        patch("guitar_helper.run_batch.init_db"),
        patch("guitar_helper.run_batch.AnalysisPipeline", return_value=mock_pipeline),
        patch("guitar_helper.run_batch.AudioSeparator"),
    ):
        MockLoader.return_value.load.return_value = (5000, "fakehash")

        from guitar_helper.run_batch import main
        sys.argv = ["run_batch", str(tmp_path), "--reanalyze"]
        main()

    mock_pipeline.run.assert_called_once()


# ---------------------------------------------------------------------------
# Error resilience — one failed file does not abort the batch
# ---------------------------------------------------------------------------

def test_batch_continues_after_failure(tmp_path, capsys):
    _write_wav(tmp_path / "good.wav")
    _write_wav(tmp_path / "also_good.wav")

    mock_store = MagicMock()
    mock_store.get_segments.return_value = []

    call_count = 0

    def _side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated decode error")
        return [_make_segment("h")]

    mock_pipeline = MagicMock()
    mock_pipeline.run.side_effect = _side_effect

    with (
        patch("guitar_helper.run_batch.AudioLoader") as MockLoader,
        patch("guitar_helper.run_batch.SQLiteSegmentStore", return_value=mock_store),
        patch("guitar_helper.run_batch.init_db"),
        patch("guitar_helper.run_batch.AnalysisPipeline", return_value=mock_pipeline),
        patch("guitar_helper.run_batch.AudioSeparator"),
    ):
        MockLoader.return_value.load.return_value = (5000, "h")

        from guitar_helper.run_batch import main
        sys.argv = ["run_batch", str(tmp_path)]
        main()

    assert call_count == 2  # attempted both files
    captured = capsys.readouterr()
    assert "FAILED" in captured.out
    assert "OK" in captured.out


# ---------------------------------------------------------------------------
# D11 — --no-separate removed
# ---------------------------------------------------------------------------

def test_no_separate_flag_removed(tmp_path):
    from guitar_helper.run_batch import main
    sys.argv = ["run_batch", str(tmp_path), "--no-separate"]
    with pytest.raises(SystemExit):
        main()


def test_audio_extensions_matches_supported_suffixes():
    assert _AUDIO_EXTENSIONS == SUPPORTED_SUFFIXES
