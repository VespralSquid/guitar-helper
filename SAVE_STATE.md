# Save State — Guitar Helper
_Last updated: 2026-07-13_

Status: **Phase 1+2 DONE. Phase 3 DONE. Phase 4: M0 + O1 + O2 DONE. ISSUE-005 RESOLVED** (user ear-confirmed; waveform→SegmentTimeline). Branch `phase3-readiness`, NOT pushed, all Phase-4 work uncommitted. Next: commit, then O3 (Analysis rework). "Play next" clarity polish deferred to fancy-UI pass.

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

## Phase 4 UI M1-M3 (DONE)
- **DB layer extended:** Track dataclass + list_tracks() (LEFT JOIN segments for correction-progress, zero-segment tracks included). Schema still v6.
- **UI package built:** `guitar_helper/ui/` — theme.py (colors/QSS/sizes, zero assets, icon() → None, callers fallback to text), controllers.py (PlaybackController wraps Application, owns loop-current-segment logic), transport.py (TransportControls widget), panels/library_panel.py (track list via list_tracks), state/editor_state.py (Qt-free EditorState: load_track/clear/select/segment_at; M4/M5 adds relabel/boundary/confirm/merge/save/discard; StateEvent.kind pre-declares all future kinds, only "loaded"/"selection" emitted so far), state/state_bridge.py (EditorStateBridge translates StateEvent → Qt signals), views/waveform_view.py (pyqtgraph envelope downsample 3000 cols, playhead, click-to-seek), views/segment_overlay.py (LinearRegionItem tone bands, non-draggable; M4 adds dragging), models/qt_adapters.py (SegmentTableModel), main_window.py (QMainWindow, File→Open, single _pos_timer 50ms; _viz/_dispatch deferred), app.py (build QApplication + Application + MainWindow), run_ui.py (entrypoint: `python -m guitar_helper.run_ui --mock`).
- **Click handler unified:** waveform click → both controller.seek() AND editor_state.select(segment_at(ms)) in ONE path (simpler than per-band handlers).
- **Tests:** test_editor_state.py (11 tests: load/select/segment_at/defensive-copy), +4 to test_repository.py (list_tracks), test_ui_smoke.py (4 pytest-qt headless: window builds, library populates, row selection syncs, timer starts/stops). Total 152 passing, ruff clean.
- **Manual verification:** live on library.db (9 tracks, 129 segments) — launched `python -m guitar_helper.run_ui --mock`, confirmed library shows correct correction-progress (e.g., carry_on_my_wayward_son [16/16]), double-clicking a track renders envelope + 16 tone bands + segment table, click-in-waveform seeks + highlights band + syncs table row. Closed cleanly (exit 0, no stderr).
## Phase 4 overhaul — M0 + O1 + O2 (DONE 2026-07-13)
- New plan: docs/plans/phase4-overhaul-plan.md SUPERSEDES phase4-ui-plan.md roadmap. §6 = user acceptance criteria AC1-8 + dispositions. Old doc = design record for O3 editor internals.
- User decisions: double-click plays (single selects); now-playing TEXT-only (cover art deferred — read embedded iTunes/Bandcamp tags via mutagen later); recalibrate-in-UI DROPPED (stays CLI, design parked in plan's Deferred); Media mode ABSORBED (3 modes: Home/Analysis/Output); playlists persist (schema v8), queue in-memory; after O2 RE-ASSESS with user before O3.
- M0: Application.load split → decode() (hash+decode, thread-safe, no DB) + attach() (main thread: validate THEN stop() old engine/dispatcher THEN wire new). play/pause/seek None-guarded. ui/load_worker.py LoadWorker(QThread) runs decode; MainWindow attaches on decoded signal, disables library + "Loading…" while in flight, loadFinished signal for tests. USER VERIFIED: Errors 1/2/3 in docs/debug/"List of known errors" all fixed (no freeze, no orphaned/overlaid playback).
- O1: MainWindow now shell — sidebar (QListWidget#modeSidebar) + QStackedWidget with 4 modes (Home/Media/Analysis/Output, constants MODE_*) + persistent transport strip. ui/modes/: home.py (HomeMode: stats label + LibraryPanel, trackChosen), analysis.py (AnalysisMode: waveform+overlay+segment table in splitter; signals seekRequested/rowSelected; methods load_buffer/set_segments/set_selected/set_playhead_ms), placeholders.py (MediaMode/OutputMode stubs for O2/O4). Load auto-switches to Analysis mode. Playhead repaint skipped when Analysis not visible; _last_pos_ms reset on switch-to-Analysis.
- SRP rule: shell only connects mode signals to controller/state; each mode owns its widgets.
- Tests 159 passing (was 152; +4 app lifecycle, +3 net smoke), ruff clean, smoke launch OK.
- O2 (playlist-first Home + queue sidebar): schema v7 (segments_calibration table only, store methods deferred to O3) + v8 (playlists, playlist_tracks; UNIQUE name; PK(playlist_id,file_hash) blocks dupes; ON DELETE CASCADE). IPlaylistStore ABC (create/delete/list/add/remove/get_playlist_tracks) on SQLiteSegmentStore. Track gains analysed_at ("Date added", col existed).
- Home = playlist-first: virtual "Library (all songs)" pseudo-playlist (NOT a DB row) + real playlists; song table (TrackTableModel: Title/Artist/Date added/Progress); New-playlist button; context menus (add-to-playlist, remove-from-playlist, delete playlist). LibraryPanel DELETED. Double-click song → async load + AUTOPLAY + queue seeded from shown playlist; STAYS in Home (auto-switch to Analysis removed).
- QueueState (ui/state/queue_state.py, Qt-free, injectable rng): advance(manual) honors repeat-one only on auto; shuffle keeps current first, stable un-shuffle respects removals; move/remove/play_next/play_at. QueueStateBridge in state_bridge.py.
- QueueSidebar (ui/panels/queue_sidebar.py): shell-level right sidebar all modes — now-playing text, queue list (bold ▶ current), Up/Down/Play-next/Remove/Clear, Shuffle toggle + Repeat cycle. Pure queue ops go straight to QueueState; playAtRequested → shell load pipeline.
- Shell wiring: _pending_queue applied only after successful attach; File→Open falls back to single-track queue; same-hash replay = stop()+play() (avoids CallbackStop restart subtlety); track-finished detection in _on_pos_tick (_was_playing flag + position ≥ duration−50ms) → advance(manual=False). Transport gains Prev/Next.
- Verified: 187 tests passing (was 159; +18 queue_state, +5 repository, +2 schema, +3 net smoke; smoke fixture monkeypatches Application.play — autoplay must not open a real stream), ruff clean, smoke launch OK. Now 194 tests (+7 segment_timeline) after waveform→timeline swap. LIVE library.db migrated v6→v8 on first launch: 9 tracks/129 segments/129 corrections intact.

## ISSUE-004 (OPEN) — Nolly receives PC but does not switch preset
- Break is INSIDE the plugin, not our code. PCs confirmed at Cantabile + Nolly MIDI In monitors (Channel 1, PC 0-4). See `docs/debug/ISSUE-004-nolly-program-change-no-preset-switch.md`.
- Host: Cantabile Lite (Ableton Intro abandoned — filters PC to plugins). loopMIDI required on Windows.
- **Nolly MIDI Mappings dialog vs flyout mismatch:** table shows 2 rows (cleeeen=PC0, edge of break up=PC1) but flyout shows only the cleeeen mapping live → `edge` row likely NOT committed/saved → uncommitted mappings do not fire.
- **PC numbers in plugin DON'T match app scheme:** plugin has edge=PC1; app sends edge=PC3. Only PC0 (clean) currently aligns. Must remap all 5 to clean=0/crunch=1/metal=2/edge=3/overdrive=4, ideally via MIDI Learn exact-capture, then disarm learn + save the Cantabile song.
- Top suspects: (A) VST3 ignores raw MIDI PC unless plugin's internal MIDI-PC handling is enabled, (B) MIDI Learn left armed, (C) mappings typed not learned, (D) uncommitted/wrong-PC rows.

## ISSUE-005 (FIX APPLIED 2026-07-13) — root cause CONFIRMED: pyqtgraph waveform repaint
- User observation = natural discriminating test: choppy ONLY while Analysis mode visible (O1 skips playhead repaint when Analysis hidden) → pyqtgraph 20Hz scene repaint starving audio callback of GIL. Confirmed.
- Fix: waveform REMOVED entirely (also user UX decision at post-O2 re-assess). New SegmentTimeline (ui/views/segment_timeline.py): custom QWidget paintEvent, tone-colored rects + playhead, repaints only when playhead crosses a pixel column; click-to-seek; hover tooltip = tone. waveform_view.py + segment_overlay.py DELETED; theme pg helpers (tone_brush/tone_pen/WAVEFORM_*/BAND_ALPHA*) removed, tone_qcolor added. pyqtgraph now unused (spectrum M7 would re-add).
- Also: engine viz_queue=None in Application.attach (no consumer until spectrum; was raising queue.Full every callback).
- O3 boundary dragging will land on SegmentTimeline (drag band edges), superseding old M4 InfiniteLine design (noted in phase4-overhaul-plan.md O3).
- Audible confirmation in Analysis mode PENDING user re-test. Restore point: git tag `pre-issue-005-fix` (commit 1330f25).

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
- **Phase 4 overhaul:** RE-ASSESS with user (O2 shipped) → O3 Analysis (playlist-scoped entry + header + prev/next traversal, edit ops =old M4/M5, segments_calibration store methods, waveform demoted to compact strip) → O4 Output (preset panel PC-only + dispatch log). See docs/plans/phase4-overhaul-plan.md §6.
- **Phase 5:** PyInstaller `.exe`, bundled ffmpeg, setup guide.
