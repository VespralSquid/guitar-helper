# ISSUE-005 — Choppy/slow audio playback in the PySide6 UI (Phase 4)

**Status:** Open (diagnosed, no fix applied)  
**Date:** 2026-07-08

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

## Current status
Diagnosed via code inspection. No fix applied. Pending decision: apply fixes 1+2 together (both small, additive, low-risk) and re-test manually in running UI, versus profiling first to confirm the GIL-contention hypothesis before changing anything.
