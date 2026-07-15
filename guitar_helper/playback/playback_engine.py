from __future__ import annotations

import queue
import threading

import numpy as np
import sounddevice as sd

from guitar_helper.playback.audio_buffer import AudioBuffer
from guitar_helper.playback.position_tracker import PositionTracker


class PlaybackEngine:
    """sounddevice output stream over an AudioBuffer.

    The audio callback is strictly non-blocking: it copies a chunk from the
    buffer, advances the frame cursor, mirrors it into the PositionTracker, and
    hands (cursor, chunk) to the visualization queue. No DB, MIDI, or UI calls.
    """

    def __init__(
        self,
        buffer: AudioBuffer,
        tracker: PositionTracker,
        viz_queue: queue.Queue | None = None,
        blocksize: int = 1024,
    ) -> None:
        self._buffer = buffer
        self._tracker = tracker
        self._viz_queue = viz_queue
        self._blocksize = blocksize
        self._frame = 0
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def play(self) -> None:
        if self._stream is None:
            # Default (low) latency, small blocksize: the tracker reports the END of
            # the block just queued, so it already leads the audible position by one
            # block + device latency, and MidiDispatcher adds 75ms lookahead on top.
            # Buffering does not cure GIL starvation (ISSUE-005) — it only fires
            # preset changes earlier. Keep this tight.
            self._stream = sd.OutputStream(
                samplerate=self._buffer.sr,
                channels=self._buffer.n_channels,
                blocksize=self._blocksize,
                callback=self._callback,
            )
        if not self._stream.active:
            self._stream.start()

    def pause(self) -> None:
        if self._stream is not None and self._stream.active:
            self._stream.stop()

    def seek(self, position_ms: int) -> None:
        frame = int(position_ms / 1000 * self._buffer.sr)
        frame = max(0, min(frame, self._buffer.n_frames))
        with self._lock:
            self._frame = frame
        self._tracker.set_cursor(frame)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._frame = 0
        self._tracker.set_cursor(0)

    @property
    def is_playing(self) -> bool:
        return self._stream is not None and self._stream.active

    # ------------------------------------------------------------------
    # Audio thread
    # ------------------------------------------------------------------

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:  # noqa: ANN001
        with self._lock:
            start = self._frame
            end = min(start + frames, self._buffer.n_frames)
            chunk = self._buffer.data[start:end]
            self._frame = end

        n = end - start
        outdata[:n] = chunk
        if n < frames:
            outdata[n:] = 0.0

        self._tracker.set_cursor(end)
        if self._viz_queue is not None:
            try:
                self._viz_queue.put_nowait((start, chunk))
            except queue.Full:
                pass

        if end >= self._buffer.n_frames:
            raise sd.CallbackStop
