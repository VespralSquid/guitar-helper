from __future__ import annotations

import logging
import threading

from guitar_helper.db.interfaces import IPresetStore
from guitar_helper.midi.interfaces import IMidiPort
from guitar_helper.playback.position_tracker import PositionTracker
from guitar_helper.playback.segment_lookup import SegmentLookup

logger = logging.getLogger(__name__)

_NO_DISPATCH = -1  # 'other' preset: hold current amp state, send nothing


class MidiDispatcher:
    """Drives MIDI preset changes from playback position on its own thread.

    Polls the PositionTracker, resolves the active tone via SegmentLookup
    (with lookahead so the amp switches just before a boundary), and sends a
    Program Change only when the tone changes. PC numbers come from the
    `presets` table — never hardcoded. The `other` tone holds the current
    preset (no dispatch) and is logged.
    """

    def __init__(
        self,
        store: IPresetStore,
        lookup: SegmentLookup,
        tracker: PositionTracker,
        port: IMidiPort,
        channel: int = 0,
        lookahead_ms: int = 75,
        poll_interval_s: float = 0.05,
    ) -> None:
        self._lookup = lookup
        self._tracker = tracker
        self._port = port
        self._channel = channel
        self._lookahead_ms = lookahead_ms
        self._poll_interval_s = poll_interval_s
        self._pc_by_tone = {p.tone_label: p.pc_number for p in store.get_presets()}

        self._last_tone: str | None = None
        self._last_pc: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def tick(self) -> None:
        """One poll/decide/dispatch step. Thread loop calls this; tests call it directly."""
        position_ms = self._tracker.position_ms + self._lookahead_ms
        segment = self._lookup.at(position_ms)
        tone = segment.tone_label if segment else None

        if tone == self._last_tone:
            return
        self._last_tone = tone

        if tone is None:
            return  # gap between segments — hold current preset

        pc = self._pc_by_tone.get(tone)
        if pc is None:
            logger.warning("Tone %r has no preset mapping; holding.", tone)
            return
        if pc == _NO_DISPATCH:
            logger.info("Tone 'other' at %dms - holding preset, no dispatch.", position_ms)
            return

        if pc != self._last_pc:
            self._port.send_program_change(self._channel, pc)
            self._last_pc = pc
            logger.info("Dispatched PC %d (%s) at %dms.", pc, tone, position_ms)

    def reset(self) -> None:
        """Forget the last observed tone so the next tick re-evaluates (e.g. after seek)."""
        self._last_tone = None

    # ------------------------------------------------------------------
    # Threading
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="MidiDispatcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._poll_interval_s):
            self.tick()
