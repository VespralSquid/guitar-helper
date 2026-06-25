# Save State — Guitar Helper
_Last updated: 2026-06-23_

---

## Completed

### Design / Planning
- v0.3 report — canonical design doc, fully offline, 3-tier SOLID architecture
- Full feasibility analysis: sounddevice chosen over VLC
- Multi-phase build plan documented

### Phase 1 — Foundation (DONE)
- DB layer: schema.py, interfaces.py, repository.py
- AudioLoader: load() (hash+duration), load_mono() (librosa)
- ISegmentStore.save_track() implemented
- All package __init__.py stubs created

### Version Control / CI (DONE)
- GitHub: VespralSquid/guitar-helper (private)
- GitHub Actions CI: windows-latest, Python 3.14, ruff + pytest on push/PR to main

### Multi-Agent CLI Setup (DONE)
- CLAUDE.md, /phase-plan (Opus), /quick-docs (Haiku)

### Phase 2 — Analysis Pipeline (DONE, CALIBRATION COMPLETE)
- feature_extractor.py — 24-feature matrix; extract() per-song norm (segmenter), extract_for_classification() fixed-range norm (classifier); use_hpss option
- segmenter.py — cosine-distance auto-k (ISSUE-001 resolved)
- tone_classifier.py — ThresholdClassifier; auto-loads archetypes.json if present; falls back to DEFAULT_ARCHETYPES
- pipeline.py — separates guitar stem first, then loads stem for features; separator= and use_hpss= params
- source_separator.py — ISourceSeparator ABC; NullSeparator (passthrough); AudioSeparator (htdemucs_6s, stems cached at stems/<hash>_guitar.wav, lazy torch import)
- correction/cli.py — interactive label + boundary correction; exclude/include commands for calibration-excluded tracks
- run_analysis.py — --no-separate, --hpss, --stems-dir, --model-dir, --verbose, --k
- run_batch.py — folder/playlist analysis; skip-by-hash (--reanalyze); mutagen tag reading; --recursive; summary + hashes for run_correction
- run_calibrate.py — queries manually_corrected=1 segments, re-extracts clf features from stems, computes per-tone mean, writes archetypes.json; --min-segments (default 3)
- print_db.py — dev utility
- 81/81 tests passing, ruff clean
- edge-of-breakup tone (PC4); library.db seeded
- docs/batch-analysis-plan.md saved
- docs/phase2-calibration-report.md — complete calibration workflow, segment labeling results, silence detection, archetype generation

### Calibration Sub-Phase (DONE)
- Manual corrections: 9 songs all reviewed; 129 segments set manually_corrected=1, confidence=1.0
- Silence detection: 2-pass RMS < 4% threshold; 14 segments each pass; identified stems with silence (Carry On intro, Euphoria outro)
- archetypes.json generated: clean(12), crunch(40), edge(33), metal(23) from 108 non-other labeled segments across 9 tracks; ambient uncalibrated
- Calibration exclusion feature: schema v1→v2 migration (calibration_excluded column); full ISegmentStore impl; run_correction exclude/include
- Bug detected & fixed: --no-separate caused domain mismatch (full-mix features vs stem-calibrated archetypes); reanalysis re-run with stems
- Confidence post-calibration: 0.65–0.93 (from 0.21–0.35)
- ISSUE-003: RESOLVED (edge/crunch now separated by calibrated archetypes)

### Debug Docs
- docs/debug/ISSUE-001 — k=1 bug (resolved)
- docs/debug/ISSUE-002 — per-song norm bug (resolved)
- docs/debug/ISSUE-003 — edge/crunch overlap (RESOLVED via calibration)

---

## In Progress
(none)

---

## Pending

### Phase 3 — Playback + MIDI
AudioBuffer, PlaybackEngine (sounddevice), PositionTracker, SegmentLookup, MidiDispatcher, IMidiPort/MidoPort/MockMidiPort

### Phase 4 — UI
PySide6 MainWindow, TransportControls, VisualizationBridge, SpectrumAnalyzer, WaveformView, LyricsDisplay, SegmentOverlay; LrcLib client + LRC parser; preset management panel

### Phase 5 — Distribution
PyInstaller .exe, bundled ffmpeg, setup guide, AAC/M4A validation

---

## Critical Facts

**Environment**
- Python 3.14.3, Windows 11; venv at .venv/
- ffmpeg NOW INSTALLED (v8.0.1) — MP3/M4A/AAC unblocked
- audio-separator 0.44.2 — separate install (requirements-separation.txt); htdemucs_6s.yaml cached at /tmp/audio-separator-models/
- python-rtmidi: no cp314 wheel, must build from source
- ruff.toml: target-version=py312 (no py314 target yet)

**GitHub / CI**
- Repo: VespralSquid/guitar-helper (private), CI: windows-latest

**MIDI**
- clean=PC0, crunch=PC1, metal=PC2, edge=PC3, overdrive=PC4, other=-1 (no dispatch)
- ambient REMOVED (schema v4) — deferred; edge moved PC4→PC3 (schema v5); overdrive ADDED PC4 (schema v6, mid-gain between edge/crunch)
- presets table is source of truth; never hardcode PC numbers

**Calibration**
- archetypes.json: contains clean/crunch/edge/metal (ambient removed)
- Silence threshold: 4% of track peak RMS (per-track relative)
- Calibration exclusion: run_correction exclude/include commands; accessible via ISegmentStore for future UI
- Target for next refresh: 15+ segments per tone across 7+ songs
- Domain mismatch bug resolved: features extracted from stems for both segmentation and classification

**DB**
- Schema version 2: calibration_excluded column on tracks table
- segments.tone_label FK references presets.tone_label
- segments.manually_corrected and confidence stored post-labeling
- Re-analysis always replaces all segments incl. manually corrected ones

**Audio / Pipeline**
- Stem pipeline: separate_guitar() → load_mono(stem) → extract features
- --no-separate uses full mix (NullSeparator passthrough)
- Primary target: iTunes .m4a (AAC) — now unblocked with ffmpeg installed

**Issues**
- ISSUE-001: Resolved (cosine distance auto-k)
- ISSUE-002: Resolved (fixed-range normalization)
- ISSUE-003: Resolved (edge/crunch separated by calibrated archetypes)

**Decisions (verbal, not in report)**
- Segment Correction: CLI in Phase 2 (done), embed in UI in Phase 4
- Lyrics: LrcLib API auto-fetch, fallback to local .lrc
- PyInstaller .exe is v1 goal
- Batch analysis skips already-analyzed songs by hash; --reanalyze to force
