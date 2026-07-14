# ISSUE-005 — Choppy/slow audio playback in the PySide6 UI (Phase 4)

**Status:** RESOLVED (2026-07-13) — root cause was the pyqtgraph waveform's 20 Hz scene repaint starving the audio callback of the GIL while Analysis mode was visible. Fixed by replacing the waveform with the custom-painted `SegmentTimeline`. **User audibly confirmed choppiness gone in Analysis mode.**  
**Date:** 2026-07-08 (diagnosed), 2026-07-10 (first fix attempt), 2026-07-13 (reopened → confirmed → resolved)

---

## Symptom
User reported choppy/crackly audio playback while running the new PySide6 UI (`python -m guitar_helper.run_ui --mock`) and playing back an analysed track. Playback is noticeably degraded compared to Phase 3 CLI (`run_playback.py`), which exhibited no such artifacts.

Audio dropouts are audible as random crackling/stuttering throughout playback, with subjective impression of sluggish responsiveness (slow seek/position updates).

## Root cause (GIL contention between audio callback and Qt repaint threads)
Two threads compete for the Python GIL at nearly synchronous intervals:

**1. Audio callback thread (real-time):**
- `sounddevice.OutputStream` invokes `PlaybackEngine._callback()` on PortAudio's native thread
- Callback acquires GIL to execute Python code in `guitar_helper/playback/playback_engine.py`
- Current `blocksize=1024` frames at 44.1 kHz ≈ 23 ms per block — tight real-time deadline
- No `latency` argument passed to `sd.OutputStream()` — PortAudio uses its default (minimal buffering)

**2. Main thread (Qt event loop):**
- `MainWindow._pos_timer` (QTimer) fires at 33 ms interval (`_POS_TIMER_MS = 33`)
- Tick invokes `_on_pos_tick()` → `waveform.set_playhead_ms()`, moving a pyqtgraph `InfiniteLine`
- pyqtgraph's custom `paint()` overrides run Python-level code holding the GIL during repaint

**Result:** When the main thread's pyqtgraph repaint work doesn't yield the GIL back in time, the audio callback misses its real-time deadline. PortAudio's output buffer underruns, producing audible choppiness. This did not occur in Phase 3 CLI because `run_playback.py` has no GUI event loop — only a `time.sleep()` print loop, zero GIL competition.

## What was tried
Nothing yet. This is a diagnosis from code inspection + symptom report, not yet confirmed via profiling (e.g. measuring audio callback latency variance or dropout counts).

## Proposed fixes (priority order — none applied yet)
**1. (Recommended, low-risk) Increase audio callback buffering:**
- Raise `blocksize` in `PlaybackEngine.play()` (e.g. 1024 → 2048 or 4096 frames) and/or pass `latency="high"` to `sd.OutputStream()`
- Allows PortAudio more headroom to tolerate GIL-driven scheduling delays without underrunning

**2. (Recommended, low-risk) Reduce main-thread GIL pressure:**
- Throttle `MainWindow._pos_timer` repaint frequency (e.g. skip repaint if position hasn't moved enough, or decouple repaint interval from position-read interval)
- Gives audio callback more consistent GIL access

**3. (After verification) Profile before/after:**
- Instrument `PlaybackEngine._callback()` to log time-since-last-call variance before/after applying fixes 1+2
- Rule out secondary factors (e.g. audio device sample-rate mismatch requiring host-side resampling)

## Fix applied (2026-07-10)
Applied fixes 1+2 together, no profiling done first (both changes are small, additive, and reversible).

**1. Audio callback buffering (`guitar_helper/playback/playback_engine.py`):**
- `blocksize` default raised 1024 → 2048 frames
- Added `latency="high"` to the `sd.OutputStream()` call

**2. Main-thread GIL pressure (`guitar_helper/ui/main_window.py`):**
- `_POS_TIMER_MS` raised 33 → 50 (30 Hz → 20 Hz tick rate)
- `_on_pos_tick()` now tracks `_last_pos_ms` and skips `waveform.set_playhead_ms()` (the expensive pyqtgraph repaint) when position hasn't changed since the last tick — e.g. while paused/stopped, the timer keeps running but no longer forces a repaint. `_last_pos_ms` is reset to `None` on track load so the playhead still draws at position 0.

**Verification:** ruff clean, full suite 152/152 passing, UI launches cleanly (`--mock`, 6s smoke run, no traceback). Audible confirmation that crackling is gone is pending — that has to be judged by ear during a manual playback session, not from this environment.

## Current status
Fix applied on `phase3-readiness` (restore point tagged `pre-issue-005-fix` before the change, commit `1330f25`). If the audible re-test still shows dropouts, next step is profiling per option 3 above (instrument `_callback()` timing) before trying a larger blocksize or a more invasive fix (e.g. moving pyqtgraph repaint work off critical timing, or a dedicated audio process).

## Re-test result (2026-07-13) — fix insufficient, reopened
After the M0 lifecycle fixes (teardown-on-reload + async load; Errors 1-3 in `List of known errors` verified fixed by the user), playback in the UI is still "super choppy and really slow". The 2026-07-10 changes (blocksize 2048, `latency="high"`, 50 ms timer, repaint-skip-when-unchanged) did not resolve it. Note the repaint skip only helps while paused — during playback the position changes every tick, so pyqtgraph still repaints at 20 Hz.

**Next step — discriminating test (manual, by ear):** play the same track headless via the Phase 3 CLI (`python -m guitar_helper.run_playback ...`), which has no Qt event loop.
- **Still choppy headless** → the GIL/repaint diagnosis is wrong or incomplete; suspect device/stream config (sample-rate mismatch forcing host resampling, WASAPI shared-mode behavior, blocksize) — profile `_callback()` inter-arrival variance per option 3.
- **Clean headless** → confirms UI-side GIL/repaint contention; candidate fixes, in order: throttle playhead repaint to ~10 Hz (decouple from the 50 ms position read), verify `setValue` on the `InfiniteLine` isn't invalidating the whole plot (repaint only the line's bounding rect), and as a last resort a blocking-write playback thread (`stream.write()` releases the GIL in C, unlike the Python callback) or larger blocksize (4096+).

Also worth ruling out cheaply: the viz queue (`Queue(maxsize=64)`) has no consumer in M1-M3, so once full every callback raises/catches `queue.Full` — believed negligible, but passing `None` until a consumer exists (O4 spectrum work) removes it from the equation.

## Root cause confirmed (2026-07-13, natural discriminating test)
After O2 shipped, playback initiated from Home mode is clean; the choppiness appears **only while Analysis mode is visible**. Since O1, `MainWindow._on_pos_tick` skips the playhead update unless Analysis is the current stacked widget — so the only difference between the two conditions is the pyqtgraph repaint (`InfiniteLine.setValue` → scene invalidation → Python-level `paint()` at 20 Hz holding the GIL). This is the UI-side GIL/repaint contention outcome of the discriminating test, observed in normal use rather than via the headless CLI.

**Fix (in progress):** the user also independently judged the waveform unnecessary for the app. The pyqtgraph waveform + segment overlay are replaced by `SegmentTimeline` — a custom `QWidget.paintEvent` that draws one colored rect per segment plus a playhead line, repainting only when the playhead crosses a pixel column (~few repaints/sec instead of 20 scene-graph repaints/sec, and each repaint is trivially cheap). The always-full, consumer-less viz queue is also disconnected from the engine (`viz_queue=None`) until a spectrum consumer exists. Audible confirmation in Analysis mode pending user re-test.
