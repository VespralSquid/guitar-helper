from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from guitar_helper.db.interfaces import IPresetStore
from guitar_helper.midi.interfaces import IMidiPort
from guitar_helper.playback.dispatch_log import GAP, HOLD, SEND, UNMAPPED, DispatchEvent
from guitar_helper.playback.latency_probe import DispatchProbe
from guitar_helper.playback.position_tracker import PositionTracker
from guitar_helper.playback.segment_lookup import SegmentLookup

logger = logging.getLogger(__name__)

_NO_DISPATCH = -1  # 'other' preset: hold current amp state, send nothing

DEFAULT_LOOKAHEAD_MS = 75


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
        lookahead_ms: int = DEFAULT_LOOKAHEAD_MS,
        poll_interval_s: float = 0.05,
        probe: DispatchProbe | None = None,
        log_sink: Callable[[DispatchEvent], None] | None = None,
    ) -> None:
        self._lookup = lookup
        self._tracker = tracker
        self._port = port
        self._channel = channel
        self._lookahead_ms = lookahead_ms
        self._poll_interval_s = poll_interval_s
        self._probe = probe
        self._log_sink = log_sink
        self._pc_by_tone = {p.tone_label: p.pc_number for p in store.get_presets()}

        self._last_tone: str | None = None
        self._last_pc: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def lookahead_ms(self) -> int:
        return self._lookahead_ms

    @lookahead_ms.setter
    def lookahead_ms(self, value: int) -> None:
        """Settable so the Output panel's calibration knob takes effect on the
        playing track. A plain int rebind is atomic — the dispatcher thread
        either reads the old value or the new one, never a torn state."""
        self._lookahead_ms = int(value)

    @property
    def poll_interval_s(self) -> float:
        return self._poll_interval_s

    def set_pc_map(self, pc_by_tone: dict[str, int]) -> None:
        """Replace the tone->PC snapshot after a preset edit. Takes a plain dict
        rather than the store: the dispatcher thread must never touch SQLite, so
        the caller (main thread) does the reading."""
        self._pc_by_tone = dict(pc_by_tone)

    def tick(self) -> None:
        """One poll/decide/dispatch step. Thread loop calls this; tests call it directly."""
        position_ms = self._tracker.position_ms + self._lookahead_ms
        segment = self._lookup.at(position_ms)
        tone = segment.tone_label if segment else None

        if tone == self._last_tone:
            return
        self._last_tone = tone

        if tone is None:
            self._log(position_ms, GAP, None)
            return  # gap between segments — hold current preset

        pc = self._pc_by_tone.get(tone)
        if pc is None:
            logger.warning("Tone %r has no preset mapping; holding.", tone)
            self._log(position_ms, UNMAPPED, tone)
            return
        if pc == _NO_DISPATCH:
            logger.info("Tone 'other' at %dms - holding preset, no dispatch.", position_ms)
            self._log(position_ms, HOLD, tone)
            return

        if pc != self._last_pc:
            if self._probe is not None:
                t0 = time.perf_counter()
                self._port.send_program_change(self._channel, pc)
                self._probe.send_ms.add((time.perf_counter() - t0) * 1000.0)
            else:
                self._port.send_program_change(self._channel, pc)
            self._last_pc = pc
            logger.info("Dispatched PC %d (%s) at %dms.", pc, tone, position_ms)
            self._log(position_ms, SEND, tone, pc)
        else:
            self._log(position_ms, HOLD, tone, pc)

    def _log(self, position_ms: int, kind: str, tone: str | None, pc: int | None = None) -> None:
        if self._log_sink is None:
            return
        self._log_sink(DispatchEvent(position_ms=position_ms, kind=kind, tone=tone, pc=pc))

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
        last = time.perf_counter()
        while not self._stop.wait(self._poll_interval_s):
            if self._probe is not None:
                now = time.perf_counter()
                self._probe.poll_ms.add((now - last) * 1000.0)
                last = now
            self.tick()
