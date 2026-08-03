# MIDI Dispatch Latency — Analysis and Measurement Tooling

_Written 2026-07-21, on branch `phase4-o3-analysis`, between O3 and O4._

**What this document is.** The reasoning behind the tone-switch timing model and the instrumentation built to measure it. It is deliberately written *before* the correction lands: the measurement exists precisely because the correction should be set from data, not from a guess. When the offset work ships, this doc explains why the chosen value is what it is.

**Why now.** Preset switches were judged "on time by ear" after the ISSUE-005 revert, but never measured — `ISSUE-005` explicitly noted that "no ms-level measurement was taken, and none is warranted unless a switch starts feeling early." Before building O4, the decision was to stop and calibrate, so that a bedroom player gets switches that land on the boundary without hand-tuning.

---

## 1. The timing chain

The quantity that matters is **audible sync error**: the gap between *when the tone boundary is heard in the backing track* and *when the amp actually switches*. Four links sit between those two events.

| # | Link | Effect | Measurable in-app? | Where |
|---|---|---|---|---|
| A | **Tracker lead** — the audio callback sets the cursor to the *end* of the block it just *queued*, not what is audible | App position runs **ahead** of what you hear by `output_latency + one block` | ✅ From PortAudio's DAC clock | `playback_engine.py` `_callback` |
| B | **Lookahead** — tone resolved at `position_ms + 75` | Fires the PC **early**, deterministically | ✅ It is our own constant | `midi_dispatcher.py` `tick` |
| C | **Poll jitter** — dispatcher thread wakes every 50 ms | Fires 0–50 ms **late** vs the true crossing (avg ~25 ms) | ✅ Wall interval between ticks | `midi_dispatcher.py` `_run` |
| D | **Send → audible switch** — mido → rtmidi → loopMIDI → host → plugin preset apply | Amp switches **late** by the host buffer + plugin ramp | ⚠️ Only the in-app `send()` return time | `mido_port.py` |

Link A is not a defect — it is documented intent in `PlaybackEngine.play()`. It matters here only because it is a *lead* that the lookahead constant silently stacks on top of.

## 2. The governing equation

The dispatcher fires when `tracker.position_ms + lookahead >= boundary`. Since `tracker.position_ms = audible_position + L_out`, the send happens when:

```
audible_position = boundary − lookahead − L_out   (+ poll wait)
```

The amp then switches `L_chain` later, so it actually changes tone at:

```
audible_position = boundary − lookahead − L_out + poll_wait + L_chain
```

Setting that to `boundary` (switch lands exactly on the boundary) gives the whole calibration problem in one line:

```
optimal_lookahead = L_chain + poll_wait − L_out
```

**Three consequences.**

1. **It collapses to a single knob.** Every fixed term and the average of the jittery ones sum into one offset. Tone boundaries are seconds apart, so there is no need for per-segment correction — one global dispatch offset is the correct model, and today that knob is the hardcoded `lookahead_ms=75`.

2. **The current setting is probably firing early, not late.** `L_out` is itself a lead of roughly 30–50 ms, and the 75 ms lookahead stacks on top of it. Unless `L_chain` is unexpectedly large, the net is an amp that switches tens of ms ahead of the boundary. This is consistent with "on time by ear" — a tone switch landing slightly early is imperceptible, or even preferable, because the amp is ready when the note arrives. The correction direction is therefore likely *less* lead, not more.

3. **`L_chain` is the one irreducible unknown.** Python can time everything up to `send_program_change` returning, but cannot observe when Nolly *audibly* changes timbre. That is measured by ear now, and could later be automated by an audio-loopback wizard that records the amp output and detects the timbre change.

## 3. What was built

Instrumentation for links A, C, and the in-app portion of D. The production dispatch path is unchanged — the probe is opt-in and `None` in normal runs.

- **`playback/latency_probe.py`** (new) — `Samples`, a thread-safe float collector, and `Summary` (count/mean/min/max/p95). `DispatchProbe` bundles the two sample sets the dispatcher fills.
- **`playback/playback_engine.py`** — measures link A live. `last_tracker_lead_ms` is computed in the callback as `(outputBufferDacTime − currentTime) + frames/sr`: this block's *end* plays at `outputBufferDacTime + frames/sr`, but the cursor already reads `end`. Also exposes `reported_output_latency_ms` as a coarse cross-check.
- **`playback/midi_dispatcher.py`** — optional `probe` records the wall interval between ticks (C) and the `send_program_change` return time (in-app D). Added `lookahead_ms` / `poll_interval_s` read-only properties so the report can print them without reaching into privates.
- **`application.py`** — `dispatch_probe` attribute, passed to the dispatcher at `attach()`.
- **`measure_latency.py`** (new CLI) — drives the real rig and prints the measured links plus a recommended starting offset derived from the equation above.

### Why the probe is opt-in

The audio callback is the one place in this codebase where extra work has already caused a user-visible defect (ISSUE-005: pyqtgraph's 20 Hz repaint starving the callback of the GIL). So the callback does **not** append to a list or take a lock — it performs a single float attribute write, and a sampler thread in the CLI reads that float at 50 Hz. The dispatcher thread is not the audio thread, so a lock-guarded list there is fine.

### Running it

```
.venv/Scripts/python.exe -m guitar_helper.measure_latency "music/<track>.m4a" --seconds 30
```

Requires loopMIDI plus Nolly as **VST2 or standalone** (VST3 does not accept raw Program Change — see ISSUE-004). It fires a 40-PC burst to characterise send cost, then plays while the user listens for switch timing. `--start-ms` jumps to a section with an obvious clean→dist change; `--mock` runs links A and C with no MIDI hardware.

## 4. Status and next step

Measurement tooling is complete, ruff clean, tests passing. One regression was caught during the build: an existing engine test calls `_callback` with `time_info=None`, so the DAC-clock read is guarded against a missing `time_info` (which also covers backends that zero the clock).

**Pending:** run the CLI on the real rig, then replace the hardcoded `75` with a persisted, device-adaptive `dispatch_offset_ms` computed as `L_chain + poll_wait − L_out`, with `L_chain` supplied by ear. A cheap side-win to consider at the same time: dropping `poll_interval_s` from 50 ms to ~15 ms roughly halves the *jitter* of link C at negligible cost, since a tick is only a bisect plus a dict lookup.

The user-facing calibration UI from `Phase_4_QOL_changes.md` (calibrate button, confirmation, revert-to-previous) is deliberately deferred to the QOL pass — this work is the engine underneath it.
