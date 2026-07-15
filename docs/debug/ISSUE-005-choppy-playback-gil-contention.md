# ISSUE-005 — Choppy audio playback in the PySide6 UI (Phase 4)

**Status:** RESOLVED (2026-07-13). Root cause: pyqtgraph's scene repaint, driven by the playhead at 20 Hz, held the GIL in long enough bursts to starve the audio callback thread. Fixed by deleting the pyqtgraph waveform and replacing it with the custom-painted `SegmentTimeline`. User audibly confirmed clean playback in Analysis mode.

**Timeline:** 2026-07-08 diagnosed (from code inspection) → 2026-07-10 first fix attempt (**failed**) → 2026-07-13 root cause confirmed by controlled test → resolved in `4bdf312`.

---

## Symptom
Choppy/crackly audio during playback in the PySide6 UI (`python -m guitar_helper.run_ui`), heard as random stuttering throughout a track. The Phase 3 CLI (`run_playback.py`) played the same tracks cleanly — it has no Qt event loop, only a `time.sleep()` print loop.

## Root cause

`WaveformView.set_playhead_ms()` called `InfiniteLine.setValue()`, moving a `QGraphicsItem` inside pyqtgraph's `QGraphicsScene`. That invalidated a scene region spanning the full plot height, so Qt re-invoked `paint()` on **every item intersecting it**:

- 2× `PlotDataItem` — the 3000-column min/max envelope curves
- 1× `FillBetweenItem`
- N× `LinearRegionItem` — one per segment (16 on the test track)
- the `InfiniteLine` playhead itself

Every one of those `paint()` methods is **Python code inside pyqtgraph**. The position timer fired at 20 Hz, so each second the main thread ran ~20 bursts of Python-level scene painting, holding the GIL through each burst.

Meanwhile `PlaybackEngine._callback()` is a **Python function** invoked from PortAudio's native callback thread. It must acquire the GIL to execute, and it cannot preempt the main thread mid-bytecode — it waits for a GIL release. The callback itself does almost nothing (one numpy slice copy into `outdata`); it simply could not get **scheduled** in time. Missed real-time deadline → PortAudio output underrun → audible crackle.

This is a GIL-scheduling problem, not a CPU-throughput problem. Additional cores cannot help: both threads contend for the same lock.

## How it was confirmed (controlled test, 2026-07-13)

O1/O2 produced the discriminating experiment for free. Since the mode-shell refactor, `MainWindow._on_pos_tick` only updates the playhead when Analysis is the current stacked widget:

```python
if position_ms != self._last_pos_ms:
    if self._stack.currentWidget() is self.analysis:
        self.analysis.set_playhead_ms(position_ms)
```

The user then observed: **playback started from Home is clean; the choppiness appears only while Analysis mode is visible.** Same audio engine, same blocksize, same `AudioBuffer`, same dispatcher thread, same everything — the single variable between the clean and choppy conditions is whether the pyqtgraph repaint runs. That isolates the cause more cleanly than the profiling originally planned (instrumenting `_callback()` inter-arrival variance) would have.

## The failed fix (2026-07-10) — and what its failure proved

Applied together, from the original code-inspection diagnosis:
- `PlaybackEngine` `blocksize` 1024 → 2048; added `latency="high"` to `sd.OutputStream()`
- `_POS_TIMER_MS` 33 → 50 (30 Hz → 20 Hz)
- `_on_pos_tick()` skipped the playhead repaint when position was unchanged since the last tick

It did not work — playback remained "super choppy". **The failure is itself evidence.** The attempt attacked the wrong term of the equation: it assumed the real-time deadline was too *tight*, and that more buffer headroom would let the callback ride out GIL jitter. But if roughly doubling the headroom (23 ms → 46 ms per block, plus a high-latency device buffer) doesn't fix it, then the GIL stall is not small jitter you can buffer around — its duration is **comparable to or longer than the block period**. The dominant term was the *cost and frequency of the paint work*, not the tightness of the deadline. No amount of buffering rescues a callback whose thread cannot acquire the GIL for a large fraction of every 50 ms window.

The repaint-skip half was worse than useless: **during playback the position changes on every tick**, so the skip only ever triggered while paused. It optimized the one case that was never broken.

## The fix that worked (`4bdf312`)

The pyqtgraph waveform (`views/waveform_view.py`) and overlay (`views/segment_overlay.py`) were **deleted** and replaced by `views/segment_timeline.py::SegmentTimeline`. (The user had independently judged the waveform unnecessary for the app, so nothing of value was lost.) It attacks all three levers:

1. **Cost per repaint** — a plain `QWidget.paintEvent`: one `QPainter`, one `fillRect` per segment plus a playhead rect, all thin wrappers over C++ (PySide6 releases the GIL around C++ calls). No scene graph, no per-item Python `paint()`, no 3000-point path regeneration.
2. **Repaint frequency** — pixel-gated: `set_playhead_ms()` calls `update()` only when the playhead crosses a **new pixel column**. Over a 4-minute track in a ~1100 px window that is ~1 repaint per 218 ms (≈4–5/sec), down from 20/sec.
3. **Repaint occurrence** — mode-gated in `_on_pos_tick`: zero playhead work unless Analysis is the visible mode, so playback from Home or Output costs nothing.

Also removed, as hygiene rather than cause: the engine's `viz_queue` is now passed `None` (`Application.attach`). The `Queue(maxsize=64)` had no consumer in M1–M3, so once full every audio callback paid a `queue.Full` raise/catch for nothing. **This was never the cause** — it was mode-independent and therefore could not explain a symptom that only appeared in Analysis. It will be reconnected when a spectrum consumer exists (O4).

## Verification
- User audibly confirmed choppiness is gone **in Analysis mode** (the previously failing condition), 2026-07-13.
- 194 tests passing, ruff clean.
- `tests/test_segment_timeline.py` covers the ms↔x mapping and the pixel-gating (repaint only on column change).

## Residue / follow-ups

**1. The failed fix's buffering changes — REVERTED 2026-07-14.** `blocksize=2048` and `latency="high"` had been carried into `4bdf312` and were never the fix. They are not free: `PositionTracker` is set to the **end** of the block just handed to PortAudio, so the reported position already runs *ahead* of what is audible by roughly one block plus the device output latency. `MidiDispatcher` then adds its deliberate 75 ms lookahead on top. Bigger blocks and a high-latency device buffer widen that gap, so amp preset changes fire earlier relative to the audible tone boundary.

Reverted to `blocksize=1024`, `latency` unset. **Live re-test passed** (2026-07-14, carry_on_my_wayward_son with real MIDI): no choppiness, and preset switches subjectively land on time. Judged by ear only — no ms-level measurement was taken, and none is warranted unless a switch starts feeling early. As expected, dropping the buffering caused no choppiness regression: buffering was never what fixed it.

**2. Documentation.** `docs/Report/phase4-ui-architecture.md` (written 2026-07-10) describes the pre-rework M1–M3 code and is left **unedited as a historical snapshot**, including the diagnosis that led here. Current behaviour is documented in `docs/Report/phase4-rework-report.md`.

## Lessons
- **A diagnosis from code inspection is a hypothesis, not a cause.** The 2026-07-08 write-up named the right two threads and still prescribed a fix that could not work, because it mis-attributed which side of the contention was the dominant term.
- **Prefer the discriminating test to more mitigation.** The single fact that settled this — *choppy only when Analysis is visible* — was worth more than both mitigation rounds combined, and it cost nothing to observe.
- **A real-time Python callback cannot share the GIL with a busy Qt paint path.** Keep main-thread paint work either cheap (C++-side calls) or rare (pixel/event gating). Ideally both, which is what `SegmentTimeline` does.
