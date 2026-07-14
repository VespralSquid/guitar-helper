# Phase 2 Calibration — Completion Report

_Date: 2026-06-19_

---

## Starting Point

Phase 2 analysis infrastructure was complete: source separator, feature extractor (fixed-range normalization), segmenter, ThresholdClassifier, pipeline, correction CLI, `run_batch.py`, and `run_calibrate.py` all built and tested. A full reanalysis of 9 songs using htdemucs_6s guitar stems had been run and 129 segments stored in `library.db`.

However, ISSUE-003 was still open. The classifier was using `DEFAULT_ARCHETYPES` — hand-tuned, physics-motivated estimates that had never seen real audio data. The result: every song was dominated by `edge` classifications at uniformly low confidence (0.21–0.35), with `clean`, `metal`, and `crunch` barely appearing. The archetype vectors for `edge` and `crunch` were separated by only ~0.04 in the 5-feature space used for classification, making them effectively indistinguishable.

No `archetypes.json` existed. The calibration infrastructure was built but had never been exercised.

---

## Development Process

### 1 — Segment Labeling

Reviewed segment tables for all 9 songs sequentially. Manual corrections applied via `run_correction` for songs where the auto-classification was clearly wrong (I Hate Everything About You, Gunslinger). For the remaining songs the user reviewed segment tables directly and applied corrections independently.

After individual correction passes, all 129 segments were bulk-confirmed (`manually_corrected=1, confidence=1.0`) to mark the full library as verified ground truth for calibration.

### 2 — Silence Detection (Pass 1)

A targeted RMS analysis was run across all guitar stems. For each segment, the mean RMS was computed from the cached stem and compared against the track's peak RMS. Segments falling below 4% of the track peak (>96% silent by energy) were relabeled as `other`. 14 segments were identified and updated in this pass — primarily outro fragments, boundary artifacts, and sections where no guitar is present on the stem (e.g. the Carry On intro, which is keys-only).

### 3 — Calibration Run

`run_calibrate` was run after labeling. It read all `manually_corrected=1` segments, re-extracted `extract_for_classification()` feature vectors from the cached stems, computed per-tone mean vectors, and wrote `archetypes.json`. Coverage:

| Tone | Segments |
|---|---|
| clean | 12 |
| crunch | 40 |
| edge | 33 |
| metal | 23 |

`ambient` had no examples in the library and fell back to `DEFAULT_ARCHETYPES`.

### 4 — Reanalysis (Domain Mismatch Discovered)

The first reanalysis pass was run with `--no-separate`, which routes audio through `NullSeparator` and loads the full mix for feature extraction. This was incorrect: the archetypes were derived from guitar stem features, so comparing full-mix features against stem-derived archetypes introduced a systematic domain mismatch. The mismatch produced plausible-looking but incorrect results — notably, `metal` appearing in Black Dog (a classic rock song with no metal guitar).

The reanalysis was re-run without `--no-separate`, routing through the cached guitar stems as intended.

### 5 — Silence Detection (Pass 2)

The post-reanalysis segmentation produced different boundaries (the pipeline is deterministic but segment boundaries shifted due to re-analysis). A second silence detection pass was run, identifying 14 silent segments in the new segmentation and relabeling them as `other`.

---

## Issues Encountered

**Domain mismatch (--no-separate on stem-calibrated archetypes):** The first reanalysis used the full audio mix while archetypes were computed from guitar stems. The calibration output message explicitly warned against this; the warning was missed. Consequence: one incorrect reanalysis pass, corrected by re-running with stems. No data loss.

**Pre-existing test failure (test_archetype_classifies_to_itself):** Once `archetypes.json` was written to the project root, `ThresholdClassifier()` with default arguments loaded it instead of `DEFAULT_ARCHETYPES`. The test fixture was comparing `DEFAULT_ARCHETYPES` vectors against a classifier using calibrated archetypes — naturally failing. Fixed by constructing the fixture with `calibration_path=None` to pin the test to default behavior.

**MockStore missing abstract methods:** Adding `set_calibration_excluded` and `get_calibration_excluded` to `ISegmentStore` broke `MockStore` in `test_pipeline.py`. Fixed with no-op implementations.

---

## Changes Made

### Segment Database
- Manual corrections on I Hate Everything About You (segments 2, 6 → crunch; 13 → other)
- Manual corrections on Gunslinger (segments 11, 13, 14 → crunch)
- Bulk confirmation: all 129 segments set `manually_corrected=1`
- Two silence detection passes: 14 + 14 segments relabeled as `other`

### New File: `archetypes.json`
Generated from 108 non-other labeled segments across 9 tracks. Loaded automatically by `ThresholdClassifier` on init.

### DB Schema — v1 → v2 migration
Added `calibration_excluded INTEGER NOT NULL DEFAULT 0` to `tracks` table. `init_db()` runs `_migrate_v1_to_v2()` on existing v1 databases (checks column presence before ALTER TABLE to be idempotent).

### New Feature: Calibration Exclusion
- `ISegmentStore`: added `set_calibration_excluded(file_hash, excluded)` and `get_calibration_excluded(file_hash) -> bool`
- `SQLiteSegmentStore`: implemented both
- `correction/cli.py`: `exclude` and `include` commands; exclusion status shown in header on tool open; takes effect immediately (no `s` required)
- `run_calibrate.py`: query now filters `AND t.calibration_excluded = 0`

### Test Fixes
- `test_tone_classifier.py`: fixture uses `calibration_path=None`
- `test_pipeline.py`: `MockStore` implements new abstract methods

---

## Results

Confidence improved dramatically across all songs after calibration with stem-matched reanalysis:

| Metric | Pre-calibration | Post-calibration |
|---|---|---|
| Typical confidence | 0.21–0.35 | 0.65–0.93 |
| Dominant tone | edge (in 7/9 songs) | Song-appropriate |
| Desecrate Through Reverence | crunch/edge mix | metal(14) ✓ |
| Never Too Late (3DG) | edge(13) | clean(11) ✓ |
| Gunslinger (A7X) | edge(15) | clean(3) + metal(8) ✓ |
| Euphoria (Polyphia) | mostly edge | clean(3) crunch(6) edge(4) other(1) ✓ |

ISSUE-003 is resolved. Edge and crunch are now correctly separated by the calibrated archetypes.

---

## Remaining Concerns

- **`edge` confidence is lower than other tones.** The edge archetype is the most ambiguous by nature (it sits between clean and crunch). Confidence on edge-labeled segments tends to be 0.65–0.80 vs 0.85–0.93 for metal and crunch. This is acceptable for now but worth revisiting with more edge-specific examples.
- **`ambient` is uncalibrated.** No ambient guitar sections exist in the current library. The DEFAULT_ARCHETYPES ambient entry will be used at runtime. If ambient guitar content is added, a calibration round targeting it will be needed.
- **No edge tone in several songs.** After calibration, songs like Desecrate and Never Too Late have zero edge segments. This is likely correct for those recordings, but the absence of edge calibration data from those songs means the edge archetype is derived entirely from other recordings.

---

## Plans — Phase 3

With calibration complete and the analysis pipeline stable, the next phase is **Playback + MIDI**:

- `AudioBuffer` — loads decoded audio, holds `data`, `sr`, `duration_ms`
- `PlaybackEngine` — sounddevice stream, non-blocking callback, play/pause/seek/stop
- `PositionTracker` — thread-safe position read; `get_position_ms()`, `ms_until_next_boundary()`
- `SegmentLookup` — `get(file_hash, position_ms) -> Segment | None`
- `MidiDispatcher` — polls position every ~50ms on its own thread, dispatches Program Change on tone change; holds preset on `other`
- `IMidiPort` / `MidoPort` / `MockMidiPort` — production and test MIDI interfaces

End-to-end verification target: play a song, confirm MIDI PC messages fire at correct timestamps with Neural DSP loaded in Ableton via loopMIDI.

Additional calibration rounds should be run as the library grows. Target: 15+ labeled segments per tone across 7+ songs before the next archetypes refresh.
