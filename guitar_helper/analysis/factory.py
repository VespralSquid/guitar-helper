"""Production pipeline composition, Qt-free.

The GUI needs the same wiring run_batch.main() builds by hand, and it must be
buildable from a config alone so no Qt module ever names AudioSeparator. Per
the §0 invariant the separator is always AudioSeparator — NullSeparator is
test-only and must never reach this path.
"""
from __future__ import annotations

from guitar_helper.config import AppConfig
from guitar_helper.db.interfaces import ISegmentStore

from .pipeline import AnalysisPipeline
from .source_separator import AudioSeparator
from .tone_classifier import ThresholdClassifier


def build_pipeline(
    config: AppConfig,
    store: ISegmentStore,
    *,
    verbose: bool = False,
) -> AnalysisPipeline:
    """Compose a production pipeline: AudioSeparator over config.stems_dir and
    config.model_dir, ThresholdClassifier over config.archetypes_path."""
    separator = AudioSeparator(
        cache_dir=str(config.stems_dir),
        model_dir=str(config.model_dir) if config.model_dir else None,
        verbose=verbose,
    )
    classifier = ThresholdClassifier(calibration_path=config.archetypes_path)
    return AnalysisPipeline(
        store, classifier=classifier, separator=separator, verbose=verbose,
    )
