from __future__ import annotations

import hashlib
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from pydub import AudioSegment

_SUPPORTED_NATIVE = {".wav", ".flac", ".ogg", ".aiff"}
_SUPPORTED_PYDUB  = {".mp3", ".m4a", ".aac"}


class AudioLoader:
    """Load an audio file into memory and compute its SHA-256 hash."""

    def load(self, path: str | Path) -> tuple[np.ndarray, int, int, str]:
        """
        Returns
        -------
        y           : float32 numpy array, shape (samples, channels) or (samples,)
        sr          : sample rate in Hz
        duration_ms : total duration in milliseconds
        file_hash   : SHA-256 hex digest of the raw file bytes
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

        file_hash = self._hash_file(path)
        suffix = path.suffix.lower()

        if suffix in _SUPPORTED_NATIVE:
            y, sr = sf.read(str(path), dtype="float32", always_2d=False)
        elif suffix in _SUPPORTED_PYDUB:
            y, sr = self._load_via_pydub(path)
        else:
            raise ValueError(
                f"Unsupported format '{suffix}'. "
                f"Supported: {_SUPPORTED_NATIVE | _SUPPORTED_PYDUB}"
            )

        samples = y.shape[0]
        duration_ms = int(samples / sr * 1000)
        return y, sr, duration_ms, file_hash

    def load_mono(self, path: str | Path, sr: int = 22050) -> tuple[np.ndarray, int]:
        """Load resampled mono float32 signal for feature extraction.

        Returns (y, sr) where y is 1-D float32 at the requested sample rate.
        Reliable for WAV/FLAC/OGG/AIFF only until ffmpeg is installed.
        """
        y, sr_out = librosa.load(str(Path(path)), sr=sr, mono=True)
        return y.astype(np.float32, copy=False), sr_out

    # ------------------------------------------------------------------

    @staticmethod
    def _hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _load_via_pydub(path: Path) -> tuple[np.ndarray, int]:
        audio = AudioSegment.from_file(str(path))
        sr = audio.frame_rate
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
        # Normalise to [-1, 1]
        samples /= float(2 ** (8 * audio.sample_width - 1))
        if audio.channels > 1:
            samples = samples.reshape(-1, audio.channels)
        return samples, sr
