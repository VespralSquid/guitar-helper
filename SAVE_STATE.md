# Save State — Guitar Helper
_Last updated: 2026-07-08_

Status: **Phase 1 + 2 DONE. Phase 3 (Playback + MIDI) DONE. Phase 4 UI M1-M3 DONE** (branch `phase3-readiness`, NOT pushed). Next: Phase 4 M4/M5.

---

## Phase 1 + 2 (DONE)
- Tiers: Analysis (offline) → Playback+MIDI → UI. Fully offline, Python + SQLite.
- DB, AudioLoader, FeatureExtractor (24-feat), Segmenter (cosine auto-k), ThresholdClassifier, AnalysisPipeline, stem separation, correction CLI, 4 run_* CLIs — all built + tested.
- **Stem pipeline:** extract features from guitar STEM (not full mix) for segmentation + classification. ISSUE-001/002/003 RESOLVED. `overdrive` tone added (edge/crunch confusion → 0).


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
- **Schema v6** + migrations loop. File locator re-finds moved tracks by hash. Silence gate routes low RMS to `other`. Calibration = mean.
- **CI:** `python-rtmidi` → `requirements-runtime.txt`. CI uses `requirements.txt` only; tests use `MockMidiPort`. pytest-qt added.
- **Pkgs:** `guitar_helper/midi/` (IMidiPort, MockMidiPort, MidoPort) + `guitar_helper/playback/` (AudioBuffer, PositionTracker, PlaybackEngine, SegmentLookup, VisualizationBridge, MidiDispatcher). Composition root `guitar_helper/application.py`; CLI `run_playback.py`.
- **Key design:** SegmentLookup snapshots at construction (in-memory bisect, not per-tick DB queries). MidiDispatcher own thread, 75ms lookahead, on-change-only dispatch from presets table. PlaybackEngine callback strictly non-blocking.
- **MidoPort** opens `'loopMIDI Port 1'` (app SENDS, host receives). App-side dispatch verified correct (PCs logged: carry_on_my_wayward_son shows other@98s, PC0@99.9s, PC4@113s, PC0@124.5s). **Live preset switching NOT yet working — ISSUE-004 OPEN (plugin-side).**
- Tests: 133 passing, ruff clean. MidoPort test skipped in CI (`importorskip("rtmidi")`).

## Phase 4 UI M1-M3 (DONE) — Next: M4/M5
- **DB layer extended:** Track dataclass + list_tracks() (LEFT JOIN segments for correction-progress, zero-segment tracks included). Schema still v6.
- **UI package built:** `guitar_helper/ui/` — theme.py (colors/QSS/sizes, zero assets, icon() → None, callers fallback to text), controllers.py (PlaybackController wraps Application, owns loop-current-segment logic), transport.py (TransportControls widget), panels/library_panel.py (track list via list_tracks), state/editor_state.py (Qt-free EditorState: load_track/clear/select/segment_at; M4/M5 adds relabel/boundary/confirm/merge/save/discard; StateEvent.kind pre-declares all future kinds, only "loaded"/"selection" emitted so far), state/state_bridge.py (EditorStateBridge translates StateEvent → Qt signals), views/waveform_view.py (pyqtgraph envelope downsample 3000 cols, playhead, click-to-seek), views/segment_overlay.py (LinearRegionItem tone bands, non-draggable; M4 adds dragging), models/qt_adapters.py (SegmentTableModel), main_window.py (QMainWindow, File→Open, single _pos_timer 33ms; _viz/_dispatch deferred), app.py (build QApplication + Application + MainWindow), run_ui.py (entrypoint: `python -m guitar_helper.run_ui --mock`).
- **Click handler unified:** waveform click → both controller.seek() AND editor_state.select(segment_at(ms)) in ONE path (simpler than per-band handlers).
- **Tests:** test_editor_state.py (11 tests: load/select/segment_at/defensive-copy), +4 to test_repository.py (list_tracks), test_ui_smoke.py (4 pytest-qt headless: window builds, library populates, row selection syncs, timer starts/stops). Total 152 passing, ruff clean.
- **Manual verification:** live on library.db (9 tracks, 129 segments) — launched `python -m guitar_helper.run_ui --mock`, confirmed library shows correct correction-progress (e.g., carry_on_my_wayward_son [16/16]), double-clicking a track renders envelope + 16 tone bands + segment table, click-in-waveform seeks + highlights band + syncs table row. Closed cleanly (exit 0, no stderr).
- **Milestones:** M1-M3 done. **M4 next:** relabel/boundary/confirm/exclude ops (CLI parity). **M5 next:** merge op + segments_calibration table (schema v7). M6/M7 (preset panel+dispatch log, spectrum+lyrics) deferred.

## ISSUE-004 (OPEN) — Nolly receives PC but does not switch preset
- Break is INSIDE the plugin, not our code. PCs confirmed at Cantabile + Nolly MIDI In monitors (Channel 1, PC 0-4). See `docs/debug/ISSUE-004-nolly-program-change-no-preset-switch.md`.
- Host: Cantabile Lite (Ableton Intro abandoned — filters PC to plugins). loopMIDI required on Windows.
- **Nolly MIDI Mappings dialog vs flyout mismatch:** table shows 2 rows (cleeeen=PC0, edge of break up=PC1) but flyout shows only the cleeeen mapping live → `edge` row likely NOT committed/saved → uncommitted mappings do not fire.
- **PC numbers in plugin DON'T match app scheme:** plugin has edge=PC1; app sends edge=PC3. Only PC0 (clean) currently aligns. Must remap all 5 to clean=0/crunch=1/metal=2/edge=3/overdrive=4, ideally via MIDI Learn exact-capture, then disarm learn + save the Cantabile song.
- Top suspects: (A) VST3 ignores raw MIDI PC unless plugin's internal MIDI-PC handling is enabled, (B) MIDI Learn left armed, (C) mappings typed not learned, (D) uncommitted/wrong-PC rows.

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
- **Phase 4 M4/M5:** Relabel/boundary/confirm/exclude ops (CLI parity). Merge op + segments_calibration table (schema v7). **M6/M7 deferred:** preset panel+dispatch log (ISSUE-004 fix), spectrum+lyrics.
- **Phase 5:** PyInstaller `.exe`, bundled ffmpeg, setup guide.
