"""Composition root: construct concretes, wire them through interfaces (DIP).

Long-lived singletons (segment store, MIDI port) are built once; per-track
objects (buffer, engine, tracker, lookup, dispatcher) are built by load(). The
store and port are injectable so tests substitute an in-memory store and
MockMidiPort without touching hardware.
"""
from __future__ import annotations

from pathlib import Path

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.config import AppConfig
from guitar_helper.db.interfaces import IAppStore
from guitar_helper.db.repository import SQLiteSegmentStore
from guitar_helper.db.schema import init_db
from guitar_helper.midi.interfaces import IMidiPort
from guitar_helper.midi.mido_port import DEFAULT_PORT_NAME, MidoPort
from guitar_helper.playback.audio_buffer import AudioBuffer
from guitar_helper.playback.dispatch_log import DispatchLogBuffer
from guitar_helper.playback.latency_probe import DispatchProbe
from guitar_helper.playback.midi_dispatcher import DEFAULT_LOOKAHEAD_MS, MidiDispatcher
from guitar_helper.playback.playback_engine import PlaybackEngine
from guitar_helper.playback.position_tracker import PositionTracker
from guitar_helper.playback.segment_lookup import SegmentLookup
from guitar_helper.playback.visualization_bridge import VisualizationBridge

DISPATCH_OFFSET_KEY = "dispatch_offset_ms"


class NoSegmentsError(RuntimeError):
    """Raised when a track has no analysed segments to drive playback."""


class Application:
    def __init__(
        self,
        config: AppConfig,
        *,
        store: IAppStore | None = None,
        port: IMidiPort | None = None,
        loader: AudioLoader | None = None,
        port_name: str = DEFAULT_PORT_NAME,
        channel: int = 0,
    ) -> None:
        self.config = config
        self.channel = channel
        # None in normal runs; the latency-measurement CLI sets this before
        # attach() so the dispatcher records poll jitter and send cost.
        self.dispatch_probe: DispatchProbe | None = None
        self._loader = loader or AudioLoader()
        self._conn = None
        if store is None:
            self._conn = init_db(str(config.db_path))
            store = SQLiteSegmentStore(self._conn)
        self.store = store
        self.port = port or MidoPort(port_name)
        self.viz = VisualizationBridge()
        # Survives track changes — the dispatcher is rebuilt per track, the log
        # is not, so the Output panel keeps its history across a load.
        self.dispatch_log = DispatchLogBuffer()

        self._dispatch_offset_ms = self.store.get_int_setting(
            DISPATCH_OFFSET_KEY, DEFAULT_LOOKAHEAD_MS
        )

        self.buffer: AudioBuffer | None = None
        self.tracker: PositionTracker | None = None
        self.engine: PlaybackEngine | None = None
        self.lookup: SegmentLookup | None = None
        self.dispatcher: MidiDispatcher | None = None

    @property
    def dispatch_offset_ms(self) -> int:
        """How far ahead of a tone boundary the Program Change is sent.

        The single calibration knob of `docs/Report/latency-calibration-analysis.md`
        (`L_chain + poll_wait − L_out`), persisted so a rig is calibrated once.
        """
        return self._dispatch_offset_ms

    def set_dispatch_offset_ms(self, value: int) -> None:
        """Persist the offset and apply it to the running dispatcher, so a
        calibration change is audible on the current track without a reload."""
        self._dispatch_offset_ms = int(value)
        self.store.set_setting(DISPATCH_OFFSET_KEY, str(self._dispatch_offset_ms))
        if self.dispatcher is not None:
            self.dispatcher.lookahead_ms = self._dispatch_offset_ms

    def reload_presets(self) -> None:
        """Push edited tone->PC mappings into the live dispatcher. The store read
        happens here, on the main thread — the dispatcher thread never sees SQLite."""
        if self.dispatcher is not None:
            self.dispatcher.set_pc_map(
                {p.tone_label: p.pc_number for p in self.store.get_presets()}
            )

    def decode(self, path: str | Path) -> tuple[str, AudioBuffer]:
        """Hash and fully decode a file. Touches no DB, Qt, or playback state,
        so it is safe to run on a worker thread while the main thread keeps
        the sole SQLite connection and the live runtime graph."""
        file_hash = self._loader.hash_file(path)
        buffer = AudioBuffer.from_file(path, self._loader)
        return file_hash, buffer

    def attach(self, file_hash: str, buffer: AudioBuffer, name: str = "") -> None:
        """Main thread only: validate, tear down the previous runtime graph,
        wire the new one. Validation comes first so a failed load leaves the
        current track playing untouched."""
        if not self.store.get_segments(file_hash):
            label = name or "track"
            raise NoSegmentsError(
                f"No analysed segments for {label!r} (hash {file_hash[:12]}…). "
                f"Run run_analysis first."
            )
        self.stop()
        self.buffer = buffer
        self.tracker = PositionTracker(buffer.sr)
        # viz_queue=None until a spectrum view consumes it (plan: O4/deferred) —
        # with no consumer the bounded queue fills and every audio callback
        # pays a queue.Full raise for nothing (ISSUE-005 hygiene).
        self.engine = PlaybackEngine(buffer, self.tracker, None)
        self.lookup = SegmentLookup(self.store, file_hash)
        self.dispatcher = MidiDispatcher(
            self.store, self.lookup, self.tracker, self.port,
            channel=self.channel, probe=self.dispatch_probe,
            lookahead_ms=self.dispatch_offset_ms,
            log_sink=self.dispatch_log.record,
        )

    def load(self, path: str | Path) -> str:
        file_hash, buffer = self.decode(path)
        self.attach(file_hash, buffer, name=Path(path).name)
        return file_hash

    def play(self) -> None:
        if self.engine is None or self.dispatcher is None:
            return
        self.dispatcher.start()
        self.engine.play()

    def pause(self) -> None:
        if self.engine is not None:
            self.engine.pause()

    def seek(self, position_ms: int) -> None:
        if self.engine is None or self.dispatcher is None:
            return
        self.engine.seek(position_ms)
        self.dispatcher.reset()

    def stop(self) -> None:
        if self.engine is not None:
            self.engine.stop()
        if self.dispatcher is not None:
            self.dispatcher.stop()

    def shutdown(self) -> None:
        self.stop()
        self.port.close()
        if self._conn is not None:
            self._conn.close()
