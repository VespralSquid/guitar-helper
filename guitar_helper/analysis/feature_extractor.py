from __future__ import annotations

import librosa
import numpy as np

N_FEATURES = 24  # flatness(1) + zcr(1) + rms(1) + centroid(1) + contrast(7) + mfcc(13)

# Fixed lower bounds for classification normalization (per-feature, physics-motivated).
_CLF_LO = np.array(
    [0.0, 0.0, 0.0, 0.0]          # flatness, zcr, rms, centroid
    + [0.0] * 7                    # spectral contrast bands (dB)
    + [-300.0] + [-60.0] * 12,    # mfcc[0] (log-energy), mfcc[1:13]
    dtype=np.float32,
)

# Fixed upper bounds — centroid index [3] is replaced with sr/2 at call time.
_CLF_HI = np.array(
    [0.25, 0.25, 0.25, 1.0]       # centroid placeholder overridden per call
    + [40.0] * 7                   # contrast (typical max ~40 dB)
    + [50.0] + [60.0] * 12,       # mfcc
    dtype=np.float32,
)


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """Per-song min-max normalization — preserves relative intra-song variation."""
    mins = matrix.min(axis=1, keepdims=True)
    maxs = matrix.max(axis=1, keepdims=True)
    ranges = maxs - mins
    ranges[ranges == 0] = 1  # constant rows → all zeros after subtraction
    return (matrix - mins) / ranges


def _normalize_for_classification(raw: np.ndarray, sr: int) -> np.ndarray:
    """Fixed-range normalization that preserves absolute spectral character across songs."""
    lo = _CLF_LO
    hi = _CLF_HI.copy()
    hi[3] = sr / 2  # centroid upper bound = Nyquist frequency
    ranges = hi - lo
    out = np.clip(raw, lo[:, None], hi[:, None])
    return ((out - lo[:, None]) / ranges[:, None]).astype(np.float32)


class FeatureExtractor:

    def __init__(self, hop_length: int = 512, use_hpss: bool = False) -> None:
        self._hop = hop_length
        self._use_hpss = use_hpss

    def _stack_raw(self, y: np.ndarray, sr: int) -> np.ndarray:
        """Compute and stack raw (un-normalised) features into (24, n_frames)."""
        if self._use_hpss:
            y, _ = librosa.effects.hpss(y)
        h = self._hop
        flatness  = librosa.feature.spectral_flatness(y=y, hop_length=h)
        zcr       = librosa.feature.zero_crossing_rate(y=y, hop_length=h)
        rms       = librosa.feature.rms(y=y, hop_length=h)
        centroid  = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=h)
        contrast  = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=h, n_bands=6)
        mfcc      = librosa.feature.mfcc(y=y, sr=sr, hop_length=h, n_mfcc=13)
        matrix = np.vstack([flatness, zcr, rms, centroid, contrast, mfcc]).astype(np.float32)
        assert matrix.shape[0] == N_FEATURES, (
            f"Feature row count mismatch: expected {N_FEATURES}, got {matrix.shape[0]}"
        )
        return matrix

    def extract(self, y: np.ndarray, sr: int) -> np.ndarray:
        """Return (24, n_frames) float32 with each row normalised to [0, 1] per-song.

        Use for segmentation — preserves relative intra-song feature variation.
        """
        return _normalize_rows(self._stack_raw(y, sr))

    def extract_for_classification(self, y: np.ndarray, sr: int) -> np.ndarray:
        """Return (24, n_frames) float32 normalised to fixed physical ranges.

        Use for tone classification — absolute spectral character is preserved across songs.
        """
        return _normalize_for_classification(self._stack_raw(y, sr), sr)
