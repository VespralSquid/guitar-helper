from __future__ import annotations

import queue

import numpy as np


class VisualizationBridge:
    """Bounded queue handing (cursor, chunk) from the audio thread to the UI.

    Produced by PlaybackEngine in Phase 3; consumed by the PySide6 views in
    Phase 4. Bounded + drop-on-full so a stalled consumer never blocks audio.
    """

    def __init__(self, maxsize: int = 64) -> None:
        self.queue: queue.Queue[tuple[int, np.ndarray]] = queue.Queue(maxsize=maxsize)

    def get_nowait(self) -> tuple[int, np.ndarray] | None:
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None
