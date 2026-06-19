# Save State — Guitar Helper
_Last updated: 2026-06-19_

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

### Phase 2 — Analysis Pipeline (DONE, CALIBRATION PENDING)
- feature_extractor.py — 24-feature matrix; extract() per-song norm (segmenter), extract_for_classification() fixed-range norm (classifier); use_hpss option
- segmenter.py — cosine-distance auto-k (ISSUE-001 resolved)
- tone_classifier.py — ThresholdClassifier; auto-loads archetypes.json if present; falls back to DEFAULT_ARCHETYPES
- pipeline.py — separates guitar stem first, then loads stem for features; separator= and use_hpss= params
- source_separator.py — ISourceSeparator ABC; NullSeparator (passthrough); AudioSeparator (htdemucs_6s, stems cached at stems/<hash>_guitar.wav, lazy torch import)
- correction/cli.py — interactive label + boundary correction
- run_analysis.py — --no-separate, --hpss, --stems-dir, --model-dir, --verbose, --k
- run_batch.py — folder/playlist analysis; skip-by-hash (--reanalyze); mutagen tag reading; --recursive; summary + hashes for run_correction
- run_calibrate.py — queries manually_corrected=1 segments, re-extracts clf features from stems, computes per-tone mean, writes archetypes.json; --min-segments (default 3)
- print_db.py — dev utility
- 67/67 tests passing, ruff clean
- edge-of-breakup tone (PC4); library.db seeded
- docs/batch-analysis-plan.md saved

### Debug Docs
- docs/debug/ISSUE-001 — k=1 bug (resolved)
- docs/debug/ISSUE-002 — per-song norm bug (resolved)
- docs/debug/ISSUE-003 — edge/crunch overlap (open, calibration pending)

---

## In Progress
- Full reanalysis of 9 songs with stem separation (run_batch --reanalyze, started 2026-06-19 ~15:00, ~3 min/song)
- Songs: bat_country_A7X, black_dog_SAMURAI, carry_on_my_wayward_son_Kansas, desecrate_through_reverence_A7X, euphoria_polyphia, gunslinger_A7X, I_hate_everything_about_you_3DG, let_it_die_3DG, never_too_late_3DG

---

## Pending

### Calibration (ISSUE-003 Fix A — next immediate step)
- Label segments via run_correction — target 8-12 per tone across 5+ songs
- Run run_calibrate → writes archetypes.json
- Re-batch --no-separate --reanalyze to apply calibrated archetypes
- Write ISSUE-003 resolution doc once done

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
- clean=PC0, crunch=PC1, metal=PC2, ambient=PC3, edge=PC4, other=-1 (no dispatch)
- presets table is source of truth; never hardcode PC numbers

**Calibration**
- archetypes.json: project root, auto-loaded by ThresholdClassifier on init
- Tones without enough labels fall back to DEFAULT_ARCHETYPES
- Target: 8-12 labeled segments per tone across 5+ songs
- After calibration: re-batch with --no-separate (stems cached, fast)

**DB**
- segments.tone_label FK references presets.tone_label
- Re-analysis always replaces all segments incl. manually corrected ones

**Audio / Pipeline**
- Stem pipeline: separate_guitar() → load_mono(stem) → extract features
- --no-separate uses full mix (NullSeparator passthrough)
- Primary target: iTunes .m4a (AAC) — now unblocked with ffmpeg installed

**Issues**
- ISSUE-001: Resolved (cosine distance auto-k)
- ISSUE-002: Architecture fixed (fixed-range normalization); calibration pending
- ISSUE-003: Open — edge/crunch coincident in 5-feature space; MFCCs unused; fix = archetypes.json from labeled data

**Decisions (verbal, not in report)**
- Segment Correction: CLI in Phase 2 (done), embed in UI in Phase 4
- Lyrics: LrcLib API auto-fetch, fallback to local .lrc
- PyInstaller .exe is v1 goal
- Batch analysis skips already-analyzed songs by hash; --reanalyze to force
