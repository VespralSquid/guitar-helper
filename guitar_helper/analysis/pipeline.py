from __future__ import annotations

from pathlib import Path

from guitar_helper.db.interfaces import ISegmentStore, Segment

from .audio_loader import AudioLoader
from .feature_extractor import FeatureExtractor
from .segmenter import Segmenter
from .source_separator import ISourceSeparator, NullSeparator
from .tone_classifier import BaseToneClassifier, ThresholdClassifier

_HOP = 512


class ManualCorrectionsExistError(Exception):
    """Raised when re-analysis would overwrite human-verified segments.

    Manual corrections are ground truth (calibration depends on them). Pass
    discard_corrections=True to overwrite them deliberately.
    """

    def __init__(self, file_hash: str) -> None:
        self.file_hash = file_hash
        super().__init__(
            f"Track {file_hash[:12]} has manually-corrected segments; "
            f"re-analysis would erase them. Use discard_corrections to override."
        )


class AnalysisPipeline:

    def __init__(
        self,
        store: ISegmentStore,
        classifier: BaseToneClassifier | None = None,
        separator: ISourceSeparator | None = None,
        verbose: bool = False,
        use_hpss: bool = False,
    ) -> None:
        self._store = store
        self._classifier = classifier or ThresholdClassifier()
        self._separator = separator or NullSeparator()
        self._loader = AudioLoader()
        self._extractor = FeatureExtractor(hop_length=_HOP, use_hpss=use_hpss)
        self._segmenter = Segmenter(hop_length=_HOP, verbose=verbose)

    def run(
        self,
        path: str | Path,
        title: str | None = None,
        artist: str | None = None,
        k: int | None = None,
        discard_corrections: bool = False,
    ) -> list[Segment]:
        path = Path(path)

        duration_ms, file_hash = self._loader.load(path)
        if not discard_corrections and any(
            s.manually_corrected for s in self._store.get_segments(file_hash)
        ):
            raise ManualCorrectionsExistError(file_hash)
        clf_path = self._separator.separate_guitar(path, file_hash)
        # Guitar stem drives both segmentation and classification (full mix when
        # separation is disabled), so boundaries track guitar-tone changes.
        y, sr = self._loader.load_mono(clf_path)

        feature_matrix = self._extractor.extract(y, sr)
        clf_matrix = self._extractor.extract_for_classification(y, sr)
        boundaries = self._segmenter.find_boundaries(
            feature_matrix, sr, k=k, duration_ms=duration_ms
        )

        n_frames = feature_matrix.shape[1]
        segments: list[Segment] = []
        for start_ms, end_ms in zip(boundaries[:-1], boundaries[1:]):
            f0 = max(0, min(int(round(start_ms / 1000 * sr / _HOP)), n_frames - 1))
            f1 = max(f0 + 1, min(int(round(end_ms / 1000 * sr / _HOP)), n_frames))
            mean_vec = clf_matrix[:, f0:f1].mean(axis=1)
            label, conf = self._classifier.classify(mean_vec)
            segments.append(Segment(
                id=0,
                file_hash=file_hash,
                start_ms=start_ms,
                end_ms=end_ms,
                tone_label=label,
                confidence=conf,
                manually_corrected=False,
            ))

        self._store.save_track(
            file_hash, path.name, title, artist, duration_ms, str(path.resolve())
        )
        self._store.save_segments(file_hash, segments)
        return segments
