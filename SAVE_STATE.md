# Save State — Guitar Helper
_Last updated: 2026-06-18_

---

## Completed

### Design / Planning
- v0.1 report — initial Spotify-based design (now obsolete)
- v0.1 critique — identified 15+ issues incl. Spotify API deprecation
- v0.2 report — switched offline analysis to librosa; Spotify runtime retained
- Full feasibility analysis: sounddevice (Option B) chosen over VLC for raw audio access
- v0.3 report (`Guitar_Performance_Assistant_Report_v0.3.md`) — canonical design doc, fully offline, 3-tier SOLID architecture
- Multi-phase build plan documented in plan file

### Phase 1 — Foundation (DONE)
- `requirements.txt` — all deps installed, smoke-tested
- `guitar_helper/db/schema.py` — `init_db()`, DDL, preset seed
- `guitar_helper/db/interfaces.py` — `ISegmentStore` ABC, `Segment` + `Preset` dataclasses
- `guitar_helper/db/repository.py` — `SQLiteSegmentStore`
- `guitar_helper/analysis/audio_loader.py` — `AudioLoader.load()` (soundfile + pydub, SHA-256) + `load_mono()` (librosa)
- `ISegmentStore.save_track()` implemented
- All package `__init__.py` stubs created
- Phase 1 smoke test passed (DB init, preset seed, import checks)

### Version Control / CI (DONE)
- Git repo initialised, `main` branch
- `.gitignore`, `.gitattributes`
- GitHub repo: `VespralSquid/guitar-helper` (private)
- GitHub Actions CI: `windows-latest`, Python 3.14, ruff + pytest on push/PR to main
- `ruff.toml` linting config
- `tests/` stub directory
- Initial commit pushed; CI passing

### Multi-Agent CLI Setup (DONE)
- `CLAUDE.md` — project brief, routing rules, coding conventions, debug workflow
- `.claude/commands/phase-plan.md` — `/phase-plan` spawns Opus Plan agent
- `.claude/commands/quick-docs.md` — `/quick-docs` spawns Haiku for docs

### Phase 2 — Analysis Pipeline (DONE, CALIBRATION PENDING)
- `guitar_helper/analysis/feature_extractor.py` — 24-feature matrix; `extract()` per-song normalized (segmenter), `extract_for_classification()` fixed-range normalized (classifier)
- `guitar_helper/analysis/segmenter.py` — cosine-distance auto-k (ISSUE-001 resolved)
- `guitar_helper/analysis/tone_classifier.py` — `BaseToneClassifier` ABC + `ThresholdClassifier`; archetypes calibrated to fixed-range normalization scale
- `guitar_helper/analysis/pipeline.py` — `AnalysisPipeline.run()` orchestrator; routes to two matrices
- `guitar_helper/correction/cli.py` — interactive label + boundary correction tool
- `guitar_helper/run_analysis.py` — CLI runner (--verbose, --k, --title, --artist, --db)
- `guitar_helper/run_correction.py` — CLI runner
- `print_db.py` — dev utility to dump DB contents
- 47/47 tests passing, ruff clean
- edge-of-breakup tone added (PC4); library.db seeded

### Analysis Debug Docs (DONE)
- `docs/debug/ISSUE-001-SEGMENTER_BUG_REPORT.md` — k=1 bug (resolved)
- `docs/debug/ISSUE-002-classification-normalization.md` — per-song norm bug (resolved)
- Debug documentation workflow in CLAUDE.md

### DB Contents (3 real songs analysed)
- gunslinger_A7X.wav (4:11, 8 segments — edge/crunch)
- I_hate_everything_about_you_3DG.wav (3:51, 8 segments — edge/crunch)
- carry_on_my_wayward_son_Kansas.wav (5:23, 8 segments — metal/crunch/edge)

---

## Pending

### Phase 3 — Playback + MIDI
`AudioBuffer`, `PlaybackEngine` (sounddevice), `PositionTracker`, `SegmentLookup`, `MidiDispatcher`, `IMidiPort` / `MidoPort` / `MockMidiPort`

### Phase 4 — UI
PySide6 `MainWindow`, `TransportControls`, `VisualizationBridge`, `SpectrumAnalyzer`, `WaveformView`, `LyricsDisplay`, `SegmentOverlay`; LrcLib client + LRC parser; preset management UI panel

### Phase 5 — Distribution
PyInstaller `.exe`, bundled ffmpeg, setup guide, AAC/M4A validation

---

## Critical Facts

**Environment**
- Python 3.14.3 (very new — some packages compiled from source)
- python-rtmidi built from source (no 3.14 wheel)
- ffmpeg NOT installed — WAV/FLAC/OGG work; MP3/AAC blocked until installed

**GitHub / CI**
- Repo: `VespralSquid/guitar-helper` (private)
- CI runner: `windows-latest`
- `ruff.toml`: `target-version = "py312"` (ruff has no py314 target yet; py312 safe)

**MIDI (corrected from old save)**
- clean=PC0, crunch=PC1, metal=PC2, ambient=PC3, other=-1 (no dispatch, hold current)
- `presets` table is source of truth; never hardcode PC numbers

**DB**
- `segments.tone_label` FK references `presets.tone_label`
- Custom tones require inserting into `presets` first
- Re-analysis always replaces all segments including manually corrected ones (decided during Phase 2)

**Audio**
- `AudioLoader.load()` = hash + duration (soundfile path)
- `AudioLoader.load_mono()` = librosa mono at sr=22050 (now implemented)
- Multi-channel from soundfile: shape `(samples, channels)`; librosa: mono `(samples,)`
- Primary target: iTunes `.m4a` (AAC) — requires ffmpeg

**Segmenter / Calibration**
- Segmenter verbose flag: pass `--verbose` to `run_analysis` to see k estimation diagnostics
- Calibration pass on 5–10 songs needed: measure actual fixed-range normalized feature means per tone, update ThresholdClassifier.DEFAULT_ARCHETYPES
- Classification normalization: fixed-range clip per feature (flatness/zcr/rms→[0,0.25], centroid→[0,sr/2], contrast→[0,40dB], mfcc[0]→[-300,50], mfcc[1:]→[-60,60])

**Decisions made verbally (not in report)**
- Segment Correction Tool: CLI in Phase 2 (done), embed in UI in Phase 4
- Boundary editing: both `start_ms`/`end_ms` AND label
- Lyrics: LrcLib API auto-fetch, fallback to local `.lrc`
- PyInstaller `.exe` is v1 goal
- Tone taxonomy extensible via UI (add presets at runtime, no code change needed)
