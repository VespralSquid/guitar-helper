from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.analysis.pipeline import (
    ANALYSE_STAGES,
    AnalysisCancelled,
    AnalysisPipeline,
    AnalysisResult,
    ManualCorrectionsExistError,
    Stage,
    TrackPrecheck,
)
from guitar_helper.analysis.source_separator import ISourceSeparator, NullSeparator
from guitar_helper.analysis.tone_classifier import TONE_LABELS
from guitar_helper.db.interfaces import ISegmentStore, Preset, Segment


class MockStore(ISegmentStore):
    def __init__(self) -> None:
        self._tracks: list[tuple] = []
        self._segments: list[Segment] = []

    def save_track(self, file_hash, filename, title, artist, duration_ms, source_path=None) -> None:
        self._tracks.append((file_hash, filename, title, artist, duration_ms, source_path))

    def save_segments(self, file_hash, segments) -> None:
        self._segments = [s for s in segments]

    def get_segments(self, file_hash) -> list[Segment]:
        return [s for s in self._segments if s.file_hash == file_hash]

    def get_segment(self, file_hash, position_ms) -> Segment | None:
        for s in self._segments:
            if s.file_hash == file_hash and s.start_ms <= position_ms < s.end_ms:
                return s
        return None

    def update_segment(self, segment) -> None:
        self._segments = [segment if s.id == segment.id else s for s in self._segments]

    def delete_segment(self, segment_id) -> None:
        self._segments = [s for s in self._segments if s.id != segment_id]

    def ensure_calibration_copy(self, file_hash) -> None:
        pass

    def get_calibration_segments(self, file_hash):
        return []

    def get_presets(self) -> list[Preset]:
        return []

    def save_preset(self, preset) -> None:
        pass

    def set_calibration_excluded(self, file_hash, excluded) -> None:
        pass

    def get_calibration_excluded(self, file_hash) -> bool:
        return False

    def list_tracks(self):
        return []


class _RecordingStore(MockStore):
    """MockStore that also logs write-call order and arguments, for the
    persist()-is-the-only-writer / FK-order assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, tuple]] = []

    def save_track(self, *args, **kwargs) -> None:
        self.calls.append(("save_track", args))
        super().save_track(*args, **kwargs)

    def save_segments(self, *args, **kwargs) -> None:
        self.calls.append(("save_segments", args))
        super().save_segments(*args, **kwargs)


class _ExplodingStore(ISegmentStore):
    """Every method raises. Proves analyse() reaches none of them — a
    mock call-count assertion could not rule out an untested code path."""

    def _boom(self, *args, **kwargs):
        raise AssertionError("analyse touched the store")

    save_track = _boom
    save_segments = _boom
    get_segments = _boom
    get_segment = _boom
    update_segment = _boom
    delete_segment = _boom
    ensure_calibration_copy = _boom
    get_calibration_segments = _boom
    get_presets = _boom
    save_preset = _boom
    set_calibration_excluded = _boom
    get_calibration_excluded = _boom
    list_tracks = _boom


class RecordingSeparator(ISourceSeparator):
    def __init__(self, stem_path: Path) -> None:
        self.stem_path = stem_path
        self.calls: list[tuple[Path, str]] = []

    def separate_guitar(self, path: Path, file_hash: str) -> Path:
        self.calls.append((Path(path), file_hash))
        return self.stem_path


class _RaisingSeparator(ISourceSeparator):
    def separate_guitar(self, path: Path, file_hash: str) -> Path:
        raise RuntimeError("boom")


def _seed_corrected(store: MockStore, file_hash: str) -> None:
    store._segments = [Segment(
        id=1, file_hash=file_hash, start_ms=0, end_ms=1000,
        tone_label="crunch", confidence=1.0, manually_corrected=True,
    )]


# ---------------------------------------------------------------------------
# D9 — separator is required and keyword-only
# ---------------------------------------------------------------------------

def test_constructor_requires_separator_keyword():
    store = MockStore()
    with pytest.raises(TypeError):
        AnalysisPipeline(store)
    with pytest.raises(TypeError):
        AnalysisPipeline(store, NullSeparator())
    AnalysisPipeline(store, separator=NullSeparator())


# ---------------------------------------------------------------------------
# Existing behavioural tests — regression proof the split changed nothing
# ---------------------------------------------------------------------------

def test_pipeline_run_produces_segments(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    segments = AnalysisPipeline(store, separator=NullSeparator()).run(path, title="Test", artist="Artist")

    assert len(segments) >= 1
    assert len(store._tracks) == 1
    assert len(store._segments) >= 1


def test_pipeline_first_segment_starts_at_zero(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    segments = AnalysisPipeline(store, separator=NullSeparator()).run(path)
    assert segments[0].start_ms == 0


def test_pipeline_last_segment_ends_at_duration(make_wav):
    path = make_wav(duration_s=3.0)
    duration_ms, _ = AudioLoader().load(path)
    store = MockStore()
    segments = AnalysisPipeline(store, separator=NullSeparator()).run(path)
    assert segments[-1].end_ms == duration_ms


def test_pipeline_all_labels_valid(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    segments = AnalysisPipeline(store, separator=NullSeparator()).run(path)
    for s in segments:
        assert s.tone_label in TONE_LABELS


def test_pipeline_segments_are_contiguous(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    segments = AnalysisPipeline(store, separator=NullSeparator()).run(path)
    for a, b in zip(segments[:-1], segments[1:]):
        assert a.end_ms == b.start_ms


def test_pipeline_refuses_to_overwrite_corrections(make_wav):
    path = make_wav(duration_s=3.0)
    _, file_hash = AudioLoader().load(path)
    store = MockStore()
    _seed_corrected(store, file_hash)

    with pytest.raises(ManualCorrectionsExistError):
        AnalysisPipeline(store, separator=NullSeparator()).run(path)

    # ground truth left untouched
    assert len(store._segments) == 1
    assert store._segments[0].manually_corrected


def test_pipeline_discard_corrections_overwrites(make_wav):
    path = make_wav(duration_s=3.0)
    _, file_hash = AudioLoader().load(path)
    store = MockStore()
    _seed_corrected(store, file_hash)

    segments = AnalysisPipeline(store, separator=NullSeparator()).run(path, discard_corrections=True)

    assert len(segments) >= 1
    assert not any(s.manually_corrected for s in store._segments)


def test_pipeline_uses_separated_stem_for_classification(make_wav):
    mix = make_wav("mix.wav", duration_s=3.0)
    stem = make_wav("stem.wav", duration_s=3.0)
    separator = RecordingSeparator(stem)
    store = MockStore()

    segments = AnalysisPipeline(store, separator=separator).run(mix)

    # Separator invoked once with the original mix path and the file hash.
    assert len(separator.calls) == 1
    assert separator.calls[0][0] == mix
    assert separator.calls[0][1] != ""
    # Guitar stem drives both segmentation and classification.
    assert segments[0].start_ms == 0
    assert all(s.tone_label in TONE_LABELS for s in segments)


# ---------------------------------------------------------------------------
# D10 — analyse() touches no store
# ---------------------------------------------------------------------------

def test_analyse_touches_no_store(make_wav):
    path = make_wav(duration_s=3.0)
    pipeline = AnalysisPipeline(_ExplodingStore(), separator=NullSeparator())
    result = pipeline.analyse(path)
    assert isinstance(result, AnalysisResult)


# ---------------------------------------------------------------------------
# D1 — persist() is the only writer, FK order
# ---------------------------------------------------------------------------

def test_persist_calls_save_track_before_save_segments(make_wav):
    path = make_wav(duration_s=3.0)
    store = _RecordingStore()
    pipeline = AnalysisPipeline(store, separator=NullSeparator())
    result = pipeline.analyse(path, title="Test", artist="Artist")
    pipeline.persist(result)

    assert [name for name, _ in store.calls] == ["save_track", "save_segments"]
    assert store.calls[0][1] == (
        result.file_hash, result.filename, result.title,
        result.artist, result.duration_ms, result.source_path,
    )


def test_analysis_result_is_frozen_and_correct(make_wav):
    mix = make_wav("mix.wav", duration_s=3.0)
    stem = make_wav("stem.wav", duration_s=3.0)
    separator = RecordingSeparator(stem)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=separator)

    result = pipeline.analyse(mix)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.file_hash = "x"
    assert result.filename == mix.name
    assert Path(result.source_path).is_absolute()
    assert result.stem_path == str(stem)


def test_run_returns_and_stores_the_same_segments(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=NullSeparator())

    segments = pipeline.run(path)

    assert segments == store._segments
    assert all(a is b for a, b in zip(segments, store._segments))


def test_run_refuses_before_separating(make_wav):
    path = make_wav(duration_s=3.0)
    _, file_hash = AudioLoader().load(path)
    store = MockStore()
    _seed_corrected(store, file_hash)
    separator = RecordingSeparator(path)

    with pytest.raises(ManualCorrectionsExistError):
        AnalysisPipeline(store, separator=separator).run(path)

    assert separator.calls == []


# ---------------------------------------------------------------------------
# D7/D8 — precheck() and the TOCTOU re-check inside persist()
# ---------------------------------------------------------------------------

def test_precheck_does_not_decode(make_wav, monkeypatch):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=NullSeparator())

    def _boom(self, path):
        raise AssertionError("precheck decoded the file")

    monkeypatch.setattr(AudioLoader, "load", _boom)

    pre = pipeline.precheck(path)
    assert isinstance(pre, TrackPrecheck)


def test_precheck_unknown_file(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=NullSeparator())

    pre = pipeline.precheck(path)

    assert pre.segment_count == 0
    assert pre.corrected_count == 0
    assert pre.in_library is False


def test_precheck_track_with_one_corrected_segment(make_wav):
    path = make_wav(duration_s=3.0)
    _, file_hash = AudioLoader().load(path)
    store = MockStore()
    _seed_corrected(store, file_hash)
    pipeline = AnalysisPipeline(store, separator=NullSeparator())

    pre = pipeline.precheck(path)

    assert pre.corrected_count == 1
    assert pre.in_library is True


def test_precheck_track_with_deleted_segments_reads_as_not_in_library(make_wav):
    path = make_wav(duration_s=3.0)
    _, file_hash = AudioLoader().load(path)
    store = MockStore()
    store._tracks.append((file_hash, "test.wav", None, None, 3000, str(path)))
    pipeline = AnalysisPipeline(store, separator=NullSeparator())

    pre = pipeline.precheck(path)

    assert pre.segment_count == 0
    assert pre.in_library is False


def test_persist_toctou_guard(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=NullSeparator())
    result = pipeline.analyse(path)

    _seed_corrected(store, result.file_hash)

    with pytest.raises(ManualCorrectionsExistError):
        pipeline.persist(result)
    assert len(store._segments) == 1
    assert store._segments[0].manually_corrected

    pipeline.persist(result, discard_corrections=True)
    assert not any(s.manually_corrected for s in store._segments)


# ---------------------------------------------------------------------------
# D2-D4 — progress order
# ---------------------------------------------------------------------------

def test_analyse_progress_order(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=NullSeparator())

    recorded: list[Stage] = []
    pipeline.analyse(path, progress=recorded.append)

    assert recorded == list(ANALYSE_STAGES)


def test_persist_progress_is_saving_only(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=NullSeparator())
    result = pipeline.analyse(path)

    recorded: list[Stage] = []
    pipeline.persist(result, progress=recorded.append)

    assert recorded == [Stage.SAVING]


def test_run_passes_no_progress_or_cancel_callback(make_wav, monkeypatch):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=NullSeparator())

    captured: dict = {}
    original_analyse = AnalysisPipeline.analyse

    def _spy(self, path, **kwargs):
        captured.update(kwargs)
        return original_analyse(self, path, **kwargs)

    monkeypatch.setattr(AnalysisPipeline, "analyse", _spy)

    pipeline.run(path)

    assert captured.get("progress") is None
    assert captured.get("should_cancel") is None


# ---------------------------------------------------------------------------
# D5/D6 — cancellation
# ---------------------------------------------------------------------------

def test_analyse_cancel_before_separating_raises_and_skips_separation(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    separator = RecordingSeparator(path)
    pipeline = AnalysisPipeline(store, separator=separator)

    state = {"n": 0}

    def _should_cancel():
        state["n"] += 1
        return state["n"] == 2

    with pytest.raises(AnalysisCancelled) as exc_info:
        pipeline.analyse(path, should_cancel=_should_cancel)

    assert exc_info.value.stage == Stage.SEPARATING
    assert separator.calls == []
    assert store._tracks == []
    assert store._segments == []


def test_analyse_cancel_checked_before_stage_announced(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    separator = RecordingSeparator(path)
    pipeline = AnalysisPipeline(store, separator=separator)

    recorded: list[Stage] = []
    state = {"n": 0}

    def _should_cancel():
        state["n"] += 1
        return state["n"] == 2

    with pytest.raises(AnalysisCancelled):
        pipeline.analyse(path, progress=recorded.append, should_cancel=_should_cancel)

    assert recorded == [Stage.HASHING]


def test_analyse_cancel_after_last_stage_still_returns_result(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=NullSeparator())

    state = {"n": 0}

    def _should_cancel():
        state["n"] += 1
        return state["n"] > len(ANALYSE_STAGES)

    result = pipeline.analyse(path, should_cancel=_should_cancel)

    assert isinstance(result, AnalysisResult)


def test_analyse_does_not_wrap_non_cancel_failures(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    pipeline = AnalysisPipeline(store, separator=_RaisingSeparator())

    with pytest.raises(RuntimeError, match="boom"):
        pipeline.analyse(path)
