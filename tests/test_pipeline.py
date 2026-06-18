from __future__ import annotations

from guitar_helper.analysis.pipeline import AnalysisPipeline
from guitar_helper.analysis.tone_classifier import TONE_LABELS
from guitar_helper.db.interfaces import ISegmentStore, Preset, Segment


class MockStore(ISegmentStore):
    def __init__(self) -> None:
        self._tracks: list[tuple] = []
        self._segments: list[Segment] = []

    def save_track(self, file_hash, filename, title, artist, duration_ms) -> None:
        self._tracks.append((file_hash, filename, title, artist, duration_ms))

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
    _, _, duration_ms, _ = AudioLoader().load(path)
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
