"""Measure the MIDI-dispatch latency chain on the real rig.

Plays an analysed track through the live audio device and MIDI port while
recording the parts of the tone-switch delay the app *can* see:

  A. Tracker lead   — how far the position cursor runs ahead of what you hear
                      (device output latency + one block), read from PortAudio's
                      DAC clock in the audio callback.
  C. Poll jitter    — the wall interval between dispatcher poll ticks.
  D (in-app only)   — how long ``send_program_change`` takes to return.

The one piece it CANNOT see is the rest of link D: how long your host
(loopMIDI -> Cantabile/DAW) and the amp plugin take to *audibly* switch after
the PC is sent. That is measured by ear (or, later, an audio-loopback wizard).

From the measured parts it prints a data-driven starting point for the dispatch
lookahead, replacing the blind default.

Usage:
    python -m guitar_helper.measure_latency <audio_file> [--seconds 20]
        [--port-name "loopMIDI Port 1"] [--channel 0] [--start-ms N] [--mock]
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from guitar_helper.application import Application, NoSegmentsError
from guitar_helper.config import add_config_args, config_from_args
from guitar_helper.midi.mido_port import DEFAULT_PORT_NAME, MidiPortNotFoundError
from guitar_helper.midi.mock_port import MockMidiPort
from guitar_helper.playback.latency_probe import DispatchProbe, Samples, Summary

_SAMPLE_INTERVAL_S = 0.02  # 50 Hz sampling of the engine's tracker lead
_SEND_BURST = 40           # dedicated PC sends to characterise send cost


def _fmt(summary: Summary | None, unit: str = "ms") -> str:
    if summary is None:
        return "  (no samples)"
    return (
        f"  n={summary.count:<4} mean={summary.mean:6.1f}{unit}  "
        f"min={summary.minimum:6.1f}  max={summary.maximum:6.1f}  p95={summary.p95:6.1f}"
    )


def _measure_send_cost(app: Application) -> Summary | None:
    """Fire a burst of Program Changes straight at the port and time each call.

    Harmless — it just cycles amp presets before playback — and gives a dense
    send-cost sample that the sparse natural dispatches during a song cannot.
    """
    presets = sorted(p.pc_number for p in app.store.get_presets() if p.pc_number >= 0)
    if not presets:
        return None
    samples = Samples()
    for i in range(_SEND_BURST):
        pc = presets[i % len(presets)]
        t0 = time.perf_counter()
        app.port.send_program_change(app.channel, pc)
        samples.add((time.perf_counter() - t0) * 1000.0)
        time.sleep(0.01)
    return samples.summary()


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the MIDI-dispatch latency chain.")
    parser.add_argument("audio_file", help="Path to an analysed audio file")
    parser.add_argument("--seconds", type=float, default=20.0, help="Playback measurement window")
    parser.add_argument("--port-name", default=DEFAULT_PORT_NAME, help="MIDI output port name")
    parser.add_argument("--channel", type=int, default=0, help="MIDI channel (0-15)")
    parser.add_argument("--start-ms", type=int, default=0, help="Start position in ms")
    parser.add_argument("--mock", action="store_true", help="Use a mock MIDI port (no loopMIDI)")
    add_config_args(parser)
    args = parser.parse_args()
    cfg = config_from_args(args)

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    port = MockMidiPort() if args.mock else None
    try:
        app = Application(cfg, port=port, port_name=args.port_name, channel=args.channel)
    except MidiPortNotFoundError as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)

    probe = DispatchProbe()
    app.dispatch_probe = probe

    try:
        app.load(args.audio_file)
    except NoSegmentsError as exc:
        print(exc, file=sys.stderr)
        app.shutdown()
        sys.exit(1)

    print(f"Send-cost burst ({_SEND_BURST} PCs)...")
    send_burst = _measure_send_cost(app)

    if args.start_ms:
        app.seek(args.start_ms)

    lead = Samples()
    print(f"Measuring {args.seconds:.0f}s of playback (listen for switch timing)...")
    app.play()
    deadline = time.perf_counter() + args.seconds
    try:
        while time.perf_counter() < deadline and app.engine.is_playing:
            v = app.engine.last_tracker_lead_ms
            if v > 0.0:
                lead.add(v)
            time.sleep(_SAMPLE_INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()

    lookahead = app.dispatcher.lookahead_ms
    poll_wait_ms = app.dispatcher.poll_interval_s * 1000.0 / 2.0  # avg wait after crossing
    reported = app.engine.reported_output_latency_ms
    lead_sum = lead.summary()

    print("\n" + "=" * 62)
    print("LATENCY MEASUREMENT")
    print("=" * 62)
    print("\nA. Tracker lead (app position runs ahead of audible by):")
    print(_fmt(lead_sum))
    if reported is not None:
        print(f"   PortAudio reported output latency: {reported:.1f} ms (cross-check)")
    print(f"\nC. Poll jitter (interval between dispatcher ticks; target "
          f"{app.dispatcher.poll_interval_s * 1000:.0f} ms):")
    print(_fmt(probe.poll_ms.summary()))
    print(f"   -> average wait after a boundary crossing: ~{poll_wait_ms:.0f} ms")
    print("\nD. Send cost (send_program_change return time — in-app portion only):")
    print("   burst: " + _fmt(send_burst).strip())
    print("   live:  " + _fmt(probe.send_ms.summary()).strip())

    print("\n" + "-" * 62)
    print("SYNTHESIS")
    print("-" * 62)
    lead_mean = lead_sum.mean if lead_sum else 0.0
    early = lead_mean + lookahead - poll_wait_ms
    print(f"Current lookahead constant: {lookahead} ms")
    print(
        f"\nWith it, each PC is sent about {early:.0f} ms BEFORE the audible boundary\n"
        f"(= tracker lead {lead_mean:.0f} + lookahead {lookahead} - poll wait {poll_wait_ms:.0f}),\n"
        f"MINUS your host+plugin switch lag (link D, not measurable here)."
    )
    base = poll_wait_ms - lead_mean
    print(
        f"\nTo land switches ON the boundary:  lookahead = L_chain + {poll_wait_ms:.0f} "
        f"- {lead_mean:.0f}\n"
        f"  i.e. start at ~{base:.0f} ms, then ADD your measured-by-ear host+plugin lag\n"
        f"  (typically 5-40 ms). A slightly positive result (amp switches a hair early)\n"
        f"  is usually preferable to late."
    )

    app.shutdown()


if __name__ == "__main__":
    main()
