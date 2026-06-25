# Save State — Guitar Helper
_Last updated: 2026-06-25_

Status: **Phase 1 + 2 DONE. Phase 3 readiness DONE. Phase 3 (Playback + MIDI) DONE** (branch `phase3-readiness`, NOT pushed). Next: Phase 4 UI.

---

## Phase 1 + 2 (DONE) — crucial facts only
- Tiers: Analysis (offline) → Playback+MIDI (Phase 3) → UI (Phase 4). Fully offline.
- DB layer, AudioLoader, FeatureExtractor (24-feat), Segmenter (cosine auto-k), ThresholdClassifier, AnalysisPipeline, source_separator, correction CLI, 4 run_* CLIs — all built.
- **Stem pipeline:** separate guitar stem → extract features from the STEM for BOTH segmentation and classification. Do NOT analyse full mix against stem-calibrated archetypes (domain mismatch — the old `--no-separate` bug). Stems cached `stems/<hash>_guitar.wav`.
- Classifier auto-loads `archetypes.json`, merging onto `DEFAULT_ARCHETYPES` (tones absent from the file keep their default).
- ISSUE-001 (k=1), ISSUE-002 (per-song norm), ISSUE-003 (edge/crunch overlap) — all RESOLVED. ISSUE-003 finally closed by clean labels + the `overdrive` tone (confusion now 0).
- Reports: `docs/phase2-calibration-report.md`, `docs/debug/ISSUE-00{1,2,3}`.

## Phase 3 readiness (DONE) — see `docs/phase3-readiness-report.md`
- **CI gate:** `python-rtmidi` (no cp314 wheel → source build) moved to `requirements-runtime.txt`; CI installs only `requirements.txt`. Tests use `MockMidiPort`; `mido` (pure Python) stays.
- **Schema now v6.** Migrations run via a cumulative version loop (`_apply_migrations` + `_MIGRATIONS`), not an elif ladder. v2 calibration_excluded · v3 source_path · v4 ambient removed · v5 edge PC4→PC3 · v6 overdrive added.
- **File locator:** `tracks.source_path` (absolute) persisted; `save_track` upserts (refreshes moved paths, keeps `calibration_excluded`). `analysis/file_locator.py:locate()` re-locates by content hash. `AudioLoader.hash_file` public.
- **Config:** `config.py:AppConfig` owns all resource paths from one `root` (explicit > `GUITAR_HELPER_HOME` > CWD). 4 CLIs share `add_config_args`/`config_from_args`. Backward-compatible (CWD default).
- **Silence gate:** `tone_classifier` routes low normalized-RMS (`rms_floor`, idx 2) straight to `other`.
- **Calibration statistic = MEAN** (run_calibrate). EXP-001 picked median, but on the re-labeled clean data mean wins (macro-F1 0.693 vs 0.660); median was an artifact of silent mislabels. Median is the documented fallback for noisy future data.
- 106 tests, ruff clean. Experiments (`guitar_helper/experiments/`, `docs/experiments/`, synthetic test) now committed; `results/` + `library.db.bak-*` gitignored.

## CRITICAL incident — calibration labels lost & protected
- The original 129 manual labels were WIPED by a re-analysis (2026-06-19 reset `manually_corrected`→0). Unrecoverable: OneDrive version history only had post-wipe copies; `*.db` is gitignored. Only `archetypes.json` survived (in git).
- **Root-cause fix (in place):** re-analysis now raises `ManualCorrectionsExistError` and refuses to overwrite a track with manual corrections unless `--discard-corrections`. `run_batch` reports such tracks as SKIP. **This replaces the old "re-analysis always replaces all segments" behavior — corrections are now protected.**
- Recovery: re-analyzed all 9 songs fresh, re-labeled, marked all 129 segments `manually_corrected=1`. Recalibrated all 5 tones.

## Current calibration state
- `archetypes.json`: clean, edge, overdrive, crunch, metal (mean-based). `ambient` removed.
- macro-F1 ~0.69 (was 0.434); edge/crunch confusion 0.000. Per-tone F1: metal .89, crunch .82, overdrive .71, other .69, clean .56, edge .48.
- `overdrive` has only 5 labelled segments — least robust; label more + re-run `run_calibrate` to sharpen.
- library.db: 9 tracks, 129 segments, all verified ground truth, schema v6.

## Phase 3 (Playback + MIDI) DONE
- New pkgs: `guitar_helper/midi/` (IMidiPort, MockMidiPort, MidoPort) + `guitar_helper/playback/` (AudioBuffer, PositionTracker, PlaybackEngine, SegmentLookup, VisualizationBridge, MidiDispatcher). Composition root `guitar_helper/application.py`; CLI `run_playback.py`.
- **MidoPort** opens output port `'loopMIDI Port 1'` (our app SENDS; Ableton receives). Fail-fast `MidiPortNotFoundError` if absent. Channel 0 default. Verified live: PC0-4 switch Archetype Nolly presets.
- **AudioLoader.decode()** added: native-sr STEREO, float32 (frames, channels) for sounddevice (load_mono stays analysis-only). AudioBuffer wraps it.
- **SegmentLookup snapshots segments at construction (in-memory bisect), does NOT query the DB per-tick.** Critical: SQLite conn is single-thread; the dispatcher runs on its own thread. Live test initially crashed (cross-thread sqlite) → fixed by the snapshot. Regression test `test_dispatch_loop_runs_on_its_own_thread`.
- **MidiDispatcher**: own thread polls PositionTracker (~50ms), lookahead 75ms (fire when pos+lookahead >= boundary), dispatch only when PC changes vs `_last_pc`; `other`(PC-1) holds + logs, no dispatch; PCs from presets table. `reset()` clears `_last_tone` after seek.
- **PlaybackEngine** callback strictly non-blocking: copies chunk, advances frame, mirrors PositionTracker, `put_nowait` to viz queue (drop on full). Holds NO store/port (structural invariant test). play/pause/seek(clamped)/stop.
- Tests: 133 passing (was 106; +27 Phase 3), ruff clean. MidoPort fail-fast test `importorskip("rtmidi")` so CI skips it.
- Live E2E (carry_on_my_wayward_son): other@98s held, PC0@99.9s, PC4@113s, PC0@124.5s — correct, on-change-only.

## MIDI preset mapping (presets table = source of truth; never hardcode)
- clean=PC0, crunch=PC1, metal=PC2, edge=PC3, overdrive=PC4, other=-1 (no dispatch).
- `ambient` deferred (re-add later as a custom preset on a free PC). `overdrive` = mid-gain between edge/crunch.

## Environment
- Python 3.14.3, Windows 11; venv `.venv/`. Repo is under OneDrive (version history exists but did NOT save the labels; `*.db` gitignored).
- ffmpeg installed (v8.0.1) — MP3/M4A/AAC unblocked. Primary target: iTunes .m4a.
- `audio-separator` 0.44.2 — separate install (`requirements-separation.txt`); htdemucs_6s.
- `python-rtmidi`: no cp314 wheel → meson source build; runtime-only (not in CI).
- `ruff.toml` target-version=py312 (py314 bump deferred).
- GitHub: VespralSquid/guitar-helper (private); CI windows-latest, py3.14, ruff + pytest.

## Deferred (non-blocking)
- Hygiene: remove `library_separated.db` + `batch_reanalysis.log`; move `print_db.py` into package; cache the 2× librosa feature stack in `pipeline.py`; bump ruff to py314.
- QOL interactive segment editor → **Phase 4** (pinned; needs PlaybackEngine + WaveformView/SegmentOverlay). See `docs/new feature specs/Phase_2_QOL_Segmenting.md`. Feature 2 (stem cache expiry) not yet evaluated.

## Decisions (verbal, not in code/report)
- Calibration statistic = mean (median = fallback for noisy data).
- Segment correction stays CLI for now; visual editor is a Phase 4 feature.
- Lyrics: LrcLib auto-fetch, local `.lrc` fallback. PyInstaller `.exe` is the v1 goal.
- Batch analysis skips already-analyzed songs by hash; `--reanalyze` to force (now blocked on corrected tracks without `--discard-corrections`).

## Pending phases
- **Phase 4:** PySide6 UI (MainWindow, transport, waveform, spectrum, lyrics, segment overlay), preset panel, embedded segment editor. Consume VisualizationBridge queue (already produced by PlaybackEngine).
- **Phase 5:** PyInstaller `.exe`, bundled ffmpeg, setup guide.
