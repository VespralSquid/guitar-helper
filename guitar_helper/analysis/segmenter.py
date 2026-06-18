from __future__ import annotations

import librosa
import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks

_MAX_AUTO_K = 8


class Segmenter:

    def __init__(self, hop_length: int = 512, verbose: bool = False) -> None:
        self._hop = hop_length
        self._verbose = verbose

    def find_boundaries(
        self,
        feature_matrix: np.ndarray,
        sr: int,
        hop_length: int = 512,
        k: int | None = None,
        duration_ms: int | None = None,
    ) -> list[int]:
        """Return sorted, unique segment boundaries in milliseconds.

        Always includes 0 and duration_ms as first and last values.
        duration_ms should be the authoritative value from AudioLoader.load()
        to avoid a frame-rounding mismatch at the tail.
        """
        n_frames = feature_matrix.shape[1]

        if n_frames <= 1:
            end = duration_ms if duration_ms is not None else 0
            return sorted({0, end})

        if k is None:
            k_actual = self._estimate_k(feature_matrix, sr, hop_length)
            if self._verbose:
                print(f"  [segmenter] auto-detected k={k_actual} ({n_frames} frames)")
        else:
            k_actual = k
            if self._verbose:
                print(f"  [segmenter] forced k={k_actual} ({n_frames} frames)")

        k_actual = max(1, min(k_actual, n_frames))

        frame_indices = librosa.segment.agglomerative(feature_matrix, k_actual)
        times_sec = librosa.frames_to_time(frame_indices, sr=sr, hop_length=hop_length)
        boundaries_ms = [int(round(t * 1000)) for t in times_sec]

        if duration_ms is None:
            duration_ms = int(round(
                librosa.frames_to_time(n_frames, sr=sr, hop_length=hop_length) * 1000
            ))

        boundaries_ms.extend([0, duration_ms])
        return sorted(set(boundaries_ms))

    def _estimate_k(self, feature_matrix: np.ndarray, sr: int, hop_length: int) -> int:
        """Estimate segment count via cosine distance between adjacent frames.

        Frame-to-frame cosine distance spikes at tonal transitions regardless of
        song length, avoiding the fixed-kernel-size scaling problem of the
        checkerboard approach.
        """
        n_frames = feature_matrix.shape[1]

        norms = np.linalg.norm(feature_matrix, axis=0, keepdims=True)
        norms[norms == 0] = 1.0
        normed = feature_matrix / norms

        # cosine distance between consecutive frames; high = likely boundary
        similarity = (normed[:, :-1] * normed[:, 1:]).sum(axis=0)
        distance = 1.0 - np.clip(similarity, -1.0, 1.0)

        # smooth over ~1 second to suppress noise within a section
        window = max(3, sr // hop_length)
        smoothed = uniform_filter1d(distance, size=window)

        # Peaks must exceed mean+std in absolute height — not just be locally prominent.
        # Using prominence=std was inverted: high-std (structured) songs got too few peaks
        # and low-std (uniform) songs got too many.
        height_threshold = smoothed.mean() + smoothed.std()
        peaks, _ = find_peaks(smoothed, height=height_threshold, distance=window)

        if self._verbose:
            print(
                f"  [segmenter] distance peaks found={len(peaks)}"
                f"  height_threshold={height_threshold:.4f}"
                f"  mean={smoothed.mean():.4f}  std={smoothed.std():.4f}"
            )

        return max(1, min(len(peaks) + 1, min(_MAX_AUTO_K, n_frames)))
