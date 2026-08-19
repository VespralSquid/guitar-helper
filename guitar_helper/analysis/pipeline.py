from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from guitar_helper.db.interfaces import ISegmentStore, Segment

from .audio_loader import AudioLoader
from .feature_extractor import FeatureExtractor
from .segmenter import Segmenter
from .source_separator import ISourceSeparator
from .tone_classifier import BaseToneClassifier, ThresholdClassifier

_HOP = 512


class Stage(StrEnum):
    HASHING = "hashing"
    SEPARATING = "separating"
    DECODING = "decoding"
    FEATURES = "features"
    SEGMENTING = "segmenting"
    CLASSIFYING = "classifying"
    SAVING = "saving"


ANALYSE_STAGES: tuple[Stage, ...] = (
    Stage.HASHING, Stage.SEPARATING, Stage.DECODING,
    Stage.FEATURES, Stage.SEGMENTING, Stage.CLASSIFYING,
)

ProgressFn = Callable[[Stage], None]
CancelFn = Callable[[], bool]


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


class AnalysisCancelled(Exception):
    """Raised by analyse() when should_cancel() returns True at a stage boundary.

    Nothing has been persisted. A separation already in flight ran to
    completion; its stem is kept in the cache.
    """

    def __init__(self, path: Path, stage: Stage) -> None:
        self.path = Path(path)
        self.stage = stage
        super().__init__(f"Analysis of {self.path.name} cancelled before {stage}")


@dataclass(frozen=True)
class AnalysisResult:
    """Everything persist() needs. Carries no open handle and no callable, so it
    crosses a thread boundary intact. frozen is shallow: do not mutate .segments."""
    file_hash: str
    duration_ms: int
    filename: str          # Path(source).name
    source_path: str       # str(Path(source).resolve()) — resolved on the worker
    stem_path: str         # what the separator returned; evidence the invariant held
    title: str | None
    artist: str | None
    segments: list[Segment]   # id=0, not yet persisted


@dataclass(frozen=True)
class TrackPrecheck:
    """Facts about a track already in the library. Never raises."""
    file_hash: str
    segment_count: int
    corrected_count: int

    @property
    def in_library(self) -> bool:
        """True when the track has stored segments. A track row with zero
        segments (partial persist) reads as False, so it is re-analysed."""
        return self.segment_count > 0


def _guard_corrections(pre: TrackPrecheck, discard_corrections: bool) -> None:
    if not discard_corrections and pre.corrected_count:
        raise ManualCorrectionsExistError(pre.file_hash)


class AnalysisPipeline:

    def __init__(
        self,
        store: ISegmentStore,
        *,
        separator: ISourceSeparator,
        classifier: BaseToneClassifier | None = None,
        verbose: bool = False,
        use_hpss: bool = False,
    ) -> None:
        self._store = store
        self._classifier = classifier or ThresholdClassifier()
        self._separator = separator
        self._loader = AudioLoader()
        self._extractor = FeatureExtractor(hop_length=_HOP, use_hpss=use_hpss)
        self._segmenter = Segmenter(hop_length=_HOP, verbose=verbose)

    # --- main thread only (reads the store) ---------------------------------

    def precheck(self, path: str | Path) -> TrackPrecheck:
        """Hash the file (hash_file only — never load(), which decodes m4a
        through pydub for its duration) and report what the library already
        holds for it. ~0.05 s per 44 MB."""
        file_hash = self._loader.hash_file(path)
        return self._inspect(file_hash)

    def _inspect(self, file_hash: str) -> TrackPrecheck:
        segments = self._store.get_segments(file_hash)
        corrected = sum(1 for s in segments if s.manually_corrected)
        return TrackPrecheck(
            file_hash=file_hash, segment_count=len(segments), corrected_count=corrected,
        )

    # --- worker safe (touches no store, no Qt) ------------------------------

    def _enter(
        self, stage: Stage, path: Path, progress: ProgressFn | None, should_cancel: CancelFn | None,
    ) -> None:
        if should_cancel is not None and should_cancel():
            raise AnalysisCancelled(path, stage)
        if progress is not None:
            progress(stage)

    def analyse(
        self,
        path: str | Path,
        *,
        title: str | None = None,
        artist: str | None = None,
        k: int | None = None,
        progress: ProgressFn | None = None,
        should_cancel: CancelFn | None = None,
    ) -> AnalysisResult:
        """Pure computation. Touches no store, no Qt, no playback object.

        should_cancel() is polled at six stage boundaries only — before
        hashing, separating, decoding, features, segmenting, classifying —
        and progress(stage) is announced *after* that check, so a cancelled
        run never shows a stage it did not perform. If should_cancel()
        returns True, this method raises AnalysisCancelled and does no
        further work; persist() is never reached, so nothing is written to
        the database for this file. A separation already in flight is never
        interrupted — Separator.separate() exposes no abort hook — so it
        runs to completion, and the completed stem is deliberately kept in
        the stem cache. Cancel is not polled after classifying: a result
        that finished computing is returned normally and the caller is
        expected to still persist it — cancellation prevents *starting* new
        work, it does not discard finished work.

        Raises AnalysisCancelled at a stage boundary, as above.
        Raises guitar_helper.analysis.source_separator.SeparationError,
        propagated unwrapped from separate_guitar(), when separation
        produces no usable output, when post-write validation rejects the
        produced file, on insufficient disk space, or when publish fails.
        Any other failure (decode error, etc.) also propagates unwrapped.
        """
        path = Path(path)

        self._enter(Stage.HASHING, path, progress, should_cancel)
        duration_ms, file_hash = self._loader.load(path)

        self._enter(Stage.SEPARATING, path, progress, should_cancel)
        stem_path = self._separator.separate_guitar(path, file_hash)

        self._enter(Stage.DECODING, path, progress, should_cancel)
        # Guitar stem drives both segmentation and classification, so
        # boundaries track guitar-tone changes.
        y, sr = self._loader.load_mono(stem_path)

        self._enter(Stage.FEATURES, path, progress, should_cancel)
        feature_matrix = self._extractor.extract(y, sr)
        clf_matrix = self._extractor.extract_for_classification(y, sr)

        self._enter(Stage.SEGMENTING, path, progress, should_cancel)
        boundaries = self._segmenter.find_boundaries(
            feature_matrix, sr, k=k, duration_ms=duration_ms
        )

        self._enter(Stage.CLASSIFYING, path, progress, should_cancel)
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

        return AnalysisResult(
            file_hash=file_hash,
            duration_ms=duration_ms,
            filename=path.name,
            source_path=str(path.resolve()),
            stem_path=str(stem_path),
            title=title,
            artist=artist,
            segments=segments,
        )

    # --- main thread only (the sole writer) ---------------------------------

    def persist(
        self,
        result: AnalysisResult,
        *,
        discard_corrections: bool = False,
        progress: ProgressFn | None = None,
    ) -> None:
        """save_track then save_segments (FK order is mandatory). Re-runs the
        corrections guard first: a user may have corrected segments while the
        separation ran."""
        pre = self._inspect(result.file_hash)
        _guard_corrections(pre, discard_corrections)
        if progress is not None:
            progress(Stage.SAVING)
        self._store.save_track(
            result.file_hash, result.filename, result.title,
            result.artist, result.duration_ms, result.source_path,
        )
        self._store.save_segments(result.file_hash, result.segments)

    # --- unchanged public contract ------------------------------------------

    def run(
        self,
        path: str | Path,
        title: str | None = None,
        artist: str | None = None,
        k: int | None = None,
        discard_corrections: bool = False,
    ) -> list[Segment]:
        """precheck -> guard -> analyse -> persist. Signature and behaviour
        identical to before the split."""
        path = Path(path)
        pre = self.precheck(path)
        _guard_corrections(pre, discard_corrections)
        result = self.analyse(path, title=title, artist=artist, k=k)
        self.persist(result, discard_corrections=discard_corrections)
        return result.segments
