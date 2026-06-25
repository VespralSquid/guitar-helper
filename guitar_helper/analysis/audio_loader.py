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

    def load(self, path: str | Path) -> tuple[int, str]:
        """Return (duration_ms, file_hash) without decoding audio data.

        Uses soundfile.info() for native formats — no full decode.
        Pydub formats still require ffmpeg and decode to get duration.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(path)

        file_hash = self.hash_file(path)
        suffix = path.suffix.lower()

        if suffix in _SUPPORTED_NATIVE:
            info = sf.info(str(path))
            duration_ms = int(info.frames / info.samplerate * 1000)
        elif suffix in _SUPPORTED_PYDUB:
            audio = AudioSegment.from_file(str(path))
            duration_ms = int(audio.duration_seconds * 1000)
        else:
            raise ValueError(
                f"Unsupported format '{suffix}'. "
                f"Supported: {_SUPPORTED_NATIVE | _SUPPORTED_PYDUB}"
            )

        return duration_ms, file_hash

    def load_mono(self, path: str | Path, sr: int = 22050) -> tuple[np.ndarray, int]:
        """Load resampled mono float32 signal for feature extraction.

        Returns (y, sr) where y is 1-D float32 at the requested sample rate.
        Reliable for WAV/FLAC/OGG/AIFF only until ffmpeg is installed.
        """
        y, sr_out = librosa.load(str(Path(path)), sr=sr, mono=True)
        return y.astype(np.float32, copy=False), sr_out

    # ------------------------------------------------------------------

    @staticmethod
    def hash_file(path: str | Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
