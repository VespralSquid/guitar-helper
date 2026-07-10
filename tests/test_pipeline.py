from __future__ import annotations

from pathlib import Path

import pytest

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.analysis.pipeline import AnalysisPipeline, ManualCorrectionsExistError
from guitar_helper.analysis.source_separator import ISourceSeparator
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


def test_pipeline_run_produces_segments(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    segments = AnalysisPipeline(store).run(path, title="Test", artist="Artist")

    assert len(segments) >= 1
    assert len(store._tracks) == 1
    assert len(store._segments) >= 1


def test_pipeline_first_segment_starts_at_zero(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    segments = AnalysisPipeline(store).run(path)
    assert segments[0].start_ms == 0


def test_pipeline_last_segment_ends_at_duration(make_wav):
    from guitar_helper.analysis.audio_loader import AudioLoader
    path = make_wav(duration_s=3.0)
    duration_ms, _ = AudioLoader().load(path)
    store = MockStore()
    segments = AnalysisPipeline(store).run(path)
    assert segments[-1].end_ms == duration_ms


def test_pipeline_all_labels_valid(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    segments = AnalysisPipeline(store).run(path)
    for s in segments:
        assert s.tone_label in TONE_LABELS


def test_pipeline_segments_are_contiguous(make_wav):
    path = make_wav(duration_s=3.0)
    store = MockStore()
    segments = AnalysisPipeline(store).run(path)
    for a, b in zip(segments[:-1], segments[1:]):
        assert a.end_ms == b.start_ms


def _seed_corrected(store: MockStore, file_hash: str) -> None:
    store._segments = [Segment(
        id=1, file_hash=file_hash, start_ms=0, end_ms=1000,
        tone_label="crunch", confidence=1.0, manually_corrected=True,
    )]


def test_pipeline_refuses_to_overwrite_corrections(make_wav):
    path = make_wav(duration_s=3.0)
    _, file_hash = AudioLoader().load(path)
    store = MockStore()
    _seed_corrected(store, file_hash)

    with pytest.raises(ManualCorrectionsExistError):
        AnalysisPipeline(store).run(path)

    # ground truth left untouched
    assert len(store._segments) == 1
    assert store._segments[0].manually_corrected


def test_pipeline_discard_corrections_overwrites(make_wav):
    path = make_wav(duration_s=3.0)
    _, file_hash = AudioLoader().load(path)
    store = MockStore()
    _seed_corrected(store, file_hash)

    segments = AnalysisPipeline(store).run(path, discard_corrections=True)

    assert len(segments) >= 1
    assert not any(s.manually_corrected for s in store._segments)


class RecordingSeparator(ISourceSeparator):
    def __init__(self, stem_path: Path) -> None:
        self.stem_path = stem_path
        self.calls: list[tuple[Path, str]] = []

    def separate_guitar(self, path: Path, file_hash: str) -> Path:
        self.calls.append((Path(path), file_hash))
        return self.stem_path


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
