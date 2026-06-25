# Phase 3 Build Plan — Playback + MIDI (Tier 2)

_Runtime tier: local audio playback, position tracking, segment lookup, MIDI preset dispatch. Spec: `Guitar_Performance_Assistant_Report_v0.3.md` §6. Prereq: `docs/phase3-readiness-plan.md` complete._

## Goal
Play a local audio file, track position to ms precision, look up the active segment, and dispatch a MIDI Program Change to Ableton only when the tone changes — all without blocking the audio callback.

## Thread model (from report §6.3)
- **Audio thread** (sounddevice): non-blocking; writes cursor + chunks only. No DB/MIDI/UI.
- **MIDI/position thread**: reads PositionTracker, queries SegmentLookup, drives MidiDispatcher (~50ms).
- **UI thread** (Phase 4): consumes VisualizationBridge queue.

## Components

### AudioBuffer
- Full-file decode into np array at load time. Holds `data`, `sr`, `duration_ms`.
- Decoupled from playback (engine reads buffer; buffer knows nothing of files).

### PlaybackEngine
- sounddevice output stream + audio callback.
- `play()`, `pause()`, `seek(ms)`, `stop()`.
- Callback: read chunk at cursor, advance cursor, enqueue `(cursor, chunk)` to VisualizationBridge. **Strictly non-blocking.**

### PositionTracker
- `position_ms = (cursor / sr) * 1000`. Thread-safe read.
- `ms_until_next_boundary(position_ms, segments)` for MIDI lookahead.

### SegmentLookup
- Thin wrapper over existing `ISegmentStore.get_segment(file_hash, position_ms)` (query already implemented in `repository.py`).

### IMidiPort / MidoPort / MockMidiPort
- `send_program_change(channel, program)`.
- `MockMidiPort` fully substitutes `MidoPort` for tests.

### MidiDispatcher
- Own thread; reads PositionTracker, queries SegmentLookup.
- Send PC **only when active tone changes** vs last dispatched.
- Lookahead: fire when `position_ms >= segment.start_ms - lookahead_ms` (50–100ms).
- PC numbers from `presets` table — **never hardcode**.
- `other` (PC -1): hold current preset, **do not dispatch**, log the event.

### VisualizationBridge
- `queue.Queue` audio→UI handoff. Produced here, consumed in Phase 4.

### Composition root (`Application`)
- Construct all concretes, inject via interfaces (DIP per report §8.5):
  `SQLiteSegmentStore`, `MidoPort`, `ThresholdClassifier`, `PlaybackEngine`, `PositionTracker`, `SegmentLookup`, `MidiDispatcher`.

## Operational prerequisites (report §6.5)
- loopMIDI port running before process start — add a **startup check; fail fast** if absent.
- Ableton MIDI track Monitor = "In"; loopMIDI "Track" enabled in Link/MIDI prefs.
- PC messages are 0-indexed (preset 1 = program 0).

## Tests (MockMidiPort, in-memory store)
- [ ] Dispatch only on tone change.
- [ ] `other` → hold current preset, no dispatch, event logged.
- [ ] Boundary lookahead fires at the right time.
- [ ] Seek correctness (cursor + position_ms after seek).
- [ ] PositionTracker ms math.
- [ ] Audio callback non-blocking invariant (no DB/MIDI/UI calls inside).
- [ ] PC numbers sourced from `presets` table, not constants.

## Exit criteria
- File plays via sounddevice; pause/seek/stop work.
- Correct PC dispatched to loopMIDI at tone boundaries, only on change; `other` holds.
- loopMIDI absence fails fast with a clear message.
- All Phase 3 tests green in CI.
