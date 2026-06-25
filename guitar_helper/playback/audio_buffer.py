from __future__ import annotations

from pathlib import Path

import numpy as np

from guitar_helper.analysis.audio_loader import AudioLoader


class AudioBuffer:
    """Fully decoded audio held in memory, decoupled from playback.

    Knows nothing of streams or cursors — the engine reads `data` by frame
    index. `data` is float32 (frames, channels) at the file's native sr.
    """

    def __init__(self, data: np.ndarray, sr: int) -> None:
        self.data = data
        self.sr = sr

    @property
    def n_frames(self) -> int:
        return self.data.shape[0]

    @property
    def n_channels(self) -> int:
        return self.data.shape[1]

    @property
    def duration_ms(self) -> int:
        return int(self.n_frames / self.sr * 1000)

    @classmethod
    def from_file(cls, path: str | Path, loader: AudioLoader | None = None) -> AudioBuffer:
        loader = loader or AudioLoader()
        data, sr = loader.decode(path)
        return cls(data, sr)
