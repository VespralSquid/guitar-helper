"""CLI runner: play an analysed track and dispatch MIDI preset changes live.

Usage:
    python -m guitar_helper.run_playback <audio_file> [--port-name "loopMIDI Port 1"]
                                         [--channel 0] [--mock] [--start-ms N]

Requires loopMIDI running with the named port (unless --mock). The track must
already be analysed (segments in the DB) — run run_analysis first.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from guitar_helper.application import Application, NoSegmentsError
from guitar_helper.config import add_config_args, config_from_args
from guitar_helper.midi.mido_port import DEFAULT_PORT_NAME
from guitar_helper.midi.mock_port import MockMidiPort


def main() -> None:
    parser = argparse.ArgumentParser(description="Play a track and dispatch MIDI preset changes.")
    parser.add_argument("audio_file", help="Path to the analysed audio file")
    parser.add_argument("--port-name", default=DEFAULT_PORT_NAME, help="MIDI output port name")
    parser.add_argument("--channel", type=int, default=0, help="MIDI channel (0-15)")
    parser.add_argument("--start-ms", type=int, default=0, help="Start position in ms")
    parser.add_argument("--mock", action="store_true", help="Use a mock MIDI port (no loopMIDI)")
    add_config_args(parser)
    args = parser.parse_args()
    cfg = config_from_args(args)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    port = MockMidiPort() if args.mock else None
    app = Application(cfg, port=port, port_name=args.port_name, channel=args.channel)
    # The GUI falls back to a null port so label review still works without
    # loopMIDI. These CLIs exist to drive MIDI, so a null port is a hard failure
    # here — measuring dispatch latency against no-ops would print numbers that
    # look like a calibration result and are not one.
    if not app.midi_available:
        print(app.midi_error, file=sys.stderr)
        app.shutdown()
        sys.exit(2)

    try:
        app.load(args.audio_file)
    except NoSegmentsError as exc:
        print(exc, file=sys.stderr)
        app.shutdown()
        sys.exit(1)

    if args.start_ms:
        app.seek(args.start_ms)
    print(f"Playing {args.audio_file} ({app.buffer.duration_ms / 1000:.1f}s). Ctrl-C to stop.")
    app.play()
    try:
        while app.engine.is_playing:
            seg = app.lookup.at(app.tracker.position_ms)
            tone = seg.tone_label if seg else "-"
            print(f"\r{app.tracker.position_ms / 1000:6.1f}s  tone={tone:<10}", end="", flush=True)
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        print()
        app.shutdown()


if __name__ == "__main__":
    main()
