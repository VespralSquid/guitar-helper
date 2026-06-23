from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from .feature_extractor import N_FEATURES

TONE_LABELS: tuple[str, ...] = ("clean", "edge", "crunch", "metal", "ambient", "other")
_CONFIDENCE_FLOOR = 0.2

# Row layout: [0]=flatness [1]=zcr [2]=rms [3]=centroid [4:11]=contrast [11:24]=mfcc
_F, _Z, _R, _C = 0, 1, 2, 3
_CONTRAST_SLICE = slice(4, 11)  # 7 spectral contrast bands
_MID = 0.5


def _archetype(
    flatness: float,
    zcr: float,
    rms: float,
    centroid: float,
    contrast: float = _MID,
) -> list[float]:
    row = [_MID] * N_FEATURES
    row[_F] = flatness
    row[_Z] = zcr
    row[_R] = rms
    row[_C] = centroid
    # Spectral contrast: high = clear harmonics (clean), low = distorted noise (metal)
    for i in range(4, 11):
        row[i] = contrast
    return row


class BaseToneClassifier(ABC):

    @abstractmethod
    def classify(self, feature_vector: np.ndarray) -> tuple[str, float]:
        """Return (tone_label, confidence) for a 24-dim mean feature vector."""


class ThresholdClassifier(BaseToneClassifier):
    # Archetypes calibrated to fixed-range normalization in FeatureExtractor.extract_for_classification().
    # Ranges: flatness/zcr/rms→[0,0.25], centroid→[0,sr/2], contrast→[0,40dB], mfcc[0]→[-300,50], mfcc[1:]→[-60,60].
    # All values are physics-motivated estimates — run a calibration pass after gathering labelled songs.
    DEFAULT_ARCHETYPES: dict[str, list[float]] = {
        # flatness  zcr    rms    centroid  contrast
        "clean":   _archetype(0.12, 0.22, 0.30, 0.19, contrast=0.75),
        "edge":    _archetype(0.20, 0.28, 0.35, 0.22, contrast=0.67),
        "crunch":  _archetype(0.40, 0.44, 0.425, 0.275, contrast=0.46),
        "metal":   _archetype(0.60, 0.60, 0.55, 0.36, contrast=0.25),
        "ambient": _archetype(0.10, 0.10, 0.15, 0.13, contrast=0.88),
    }

    def __init__(
        self,
        archetypes: dict[str, np.ndarray] | None = None,
        calibration_path: str | Path | None = "archetypes.json",
    ) -> None:
        if archetypes is not None:
            source: dict = archetypes
        else:
            cal = Path(calibration_path) if calibration_path is not None else None
            if cal is not None and cal.exists():
                with cal.open() as f:
                    # Merge onto defaults so tones absent from the calibration file
                    # keep their default archetype instead of being dropped entirely.
                    source = {**self.DEFAULT_ARCHETYPES, **json.load(f)}
            else:
                source = self.DEFAULT_ARCHETYPES
        self._archetypes = {k: np.asarray(v, dtype=np.float32) for k, v in source.items()}

    def classify(self, feature_vector: np.ndarray) -> tuple[str, float]:
        vec = np.asarray(feature_vector, dtype=np.float32)
        dists = {label: float(np.linalg.norm(vec - arch)) for label, arch in self._archetypes.items()}
        best_label = min(dists, key=dists.__getitem__)
        worst_dist = max(dists.values())
        best_dist = dists[best_label]
        # Confidence: 1.0 = unambiguous winner, 0.0 = all archetypes equidistant.
        # Avoids the inflated scores from dividing by the theoretical max distance.
        conf = (1.0 - best_dist / worst_dist) if worst_dist > 0 else 1.0
        if conf < _CONFIDENCE_FLOOR:
            return "other", conf
        return best_label, conf
