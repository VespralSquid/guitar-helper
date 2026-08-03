# Save State — Guitar Helper
_Last updated: 2026-07-21_

Status: **Phase 1+2+3 DONE. Phase 4: M0+O1+O2 DONE, O3 DONE (committed). ISSUE-004 and ISSUE-005 both RESOLVED.** Branch `phase4-o3-analysis`, ruff clean. Latency-measurement tooling added on top of O3 (uncommitted). Next: run the measurement on the real rig, then O4.

---

## Phase 1 + 2 (DONE)
- Tiers: Analysis (offline) → Playback+MIDI → UI. DB, AudioLoader, FeatureExtractor (24-feat), Segmenter (cosine auto-k), ThresholdClassifier, AnalysisPipeline, stem separation, correction CLI. ISSUE-001/002/003 resolved via stem pipeline + overdrive tone.


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
- **MidoPort** opens `'loopMIDI Port 1'` (app SENDS, host receives). App-side dispatch verified correct (PCs logged: carry_on_my_wayward_son shows other@98s, PC0@99.9s, PC4@113s, PC0@124.5s). VST3 limitation discovered; see ISSUE-004 RESOLVED.
- Tests: 133 passing, ruff clean. MidoPort test skipped in CI (`importorskip("rtmidi")`).

## Phase 4 UI M1-M3 (DONE)
- Track dataclass + list_tracks() (LEFT JOIN segments for correction-progress). UI package built: theme/controllers/transport/panels/state/models/main_window/app/run_ui. EditorState (Qt-free) owns load/select/segment_at; Qt-bridge via EditorStateBridge + StateEvent. Tests 152 passing, live verification OK.
## Phase 4 M0 + O1 + O2 (DONE 2026-07-13)
- M0: Application.load split → decode() + attach() (main thread, validate/stop-old/wire-new). LoadWorker(QThread); user errors 1-3 fixed (no freeze/orphans).
- O1: MainWindow shell (sidebar QListWidget + QStackedWidget: Home/Analysis/Media/Output modes) + persistent transport. Analysis mode auto-selected on load; playhead repaint skipped when hidden. QueueState (Qt-free, injectable rng); QueueSidebar (now-playing text, queue list, Shuffle/Repeat/Up/Down/Play-next/Clear). ISSUE-005 FIX: SegmentTimeline replaces pyqtgraph waveform (playhead repaints only per pixel-column, not 20Hz). 
- O2: schema v7→v8 (playlists, playlist_tracks, ON DELETE CASCADE). Home = playlist-first: virtual "Library" + real playlists; double-click song → load+autoplay, queue seeded from shown playlist, STAYS in Home. Shell wiring: _pending_queue after attach; File→Open single-track fallback; track-finished via _on_pos_tick (_was_playing flag + position ≥ duration−50ms). Verified 194 tests, LIVE db migrated v6→v8 OK (9 tracks/129 segments/corrections intact).

## Phase 4 O3 (Analysis) — steps 1-9/9 DONE (committed)
- **Step 1: ISP interface split** — `ISegmentStore` (11 methods) → 4 role interfaces: `ITrackCatalog`, `ISegmentReader`, `ISegmentEditor`, `IPresetStore`. Kept deprecated `ISegmentStore` alias for backward-compat.
- **Step 2: calibration-copy store methods** — `delete_segment`, `ensure_calibration_copy` (idempotent snapshot), `get_calibration_segments` on `ISegmentEditor`.
- **Step 3: `ui/editor/validation.py`** — Qt-free EditResult, validate/apply ops (relabel/boundary/confirm). GUI relabeling = dropdown from TONE_LABELS.
- **Step 4: `ui/editor/merge.py`** — Qt-free MergePlan, find_same_tone_run, merge_run. Kept physical merge (virtual already at dispatch time).
- **Step 5: EditorState edit ops** — relabel/edit_boundary/confirm/confirm_all/merge_run/set_excluded/save/discard; dirty/excluded properties. Pattern: validate → mutate → queue pending/deleted → emit StateEvent.
- **Step 6: SegmentTimeline boundary dragging** — Added `validate_boundary_move`/`apply_boundary_move` to validation.py, `EditorState.move_boundary()`. SegmentTimeline: `boundary_at_x` hit-tester (Qt-free, unit-testable), drag state machine with live preview, cursor swap to SizeHorCursor. REGRESSION CAUGHT: existing test clicked exact pixel that became boundary hit-zone → fixed by moving test click position.
- **Step 7: AnalysisMode UI wiring** — relabel QComboBox (dropdown-only, uses textActivated to avoid feedback loop), Confirm/Confirm-All/Merge buttons, Exclude checkbox, dirty label, Save/Discard buttons. Wired through new signals to main_window.py; validation rejections surface via 4-second status-bar message. REGRESSION CAUGHT: `set_playhead_ms` accidentally dropped during rewrite, restored by fixing smoke-test AttributeError.
- **Step 8: playlist-scoped Analysis (AC7)** — Home "Analyze" button acts on selected playlist; AnalysisMode got header bar + Prev/Next for playlist paging (separate from Transport's queue Prev/Next). main_window.py gained `_enter_playlist_analysis()` + `_confirm_discard_if_dirty()` guard. SCOPING DECISION (user confirmed): dirty-guard only on new playlist Analyze/Prev/Next, NOT retrofitted onto existing O2 queue Prev/Next or Home double-click (known gap, accepted).
- **Step 9: test-gap closure + acceptance** — E2E integration tests: boundary-drag-to-store, exclude-toggle-to-store, confirm-all-to-store, discard. "Kitchen sink" test: relabel→boundary→confirm→merge→save chain. Test proving re-analysis guard causally honors corrections after GUI edit. USER verified live app: no bugs/errors (after venv activation fix).
- **O3 architecture:** ISP split decomposed ISegmentStore into 4 role interfaces; O3 store methods on ISegmentEditor. Qt-free core (validation.py, merge.py, editor_state.py) fully unit-tested; Qt layer tested via pytest-qt (isolated widgets + full E2E).

## Latency calibration — measure phase DONE 2026-07-21, correction PENDING
- Goal: PC lands on the *audible* tone boundary. Full reasoning: `docs/Report/latency-calibration-analysis.md`.
- Model: `optimal_lookahead = L_chain + poll_wait − L_out`. Collapses to ONE global knob — boundaries are seconds apart, so no per-segment correction.
- L_out (tracker lead, est 30-50ms) + hardcoded 75ms lookahead STACK → PCs likely fire **early**, not late. Correction direction is probably *less* lead. "On time by ear" is consistent with slightly-early.
- L_chain (loopMIDI→host→plugin *audible* switch) is NOT measurable from Python. Supplied by ear for now; audio-loopback wizard is the later automation.
- Built: `playback/latency_probe.py` (Samples/Summary/DispatchProbe); engine `last_tracker_lead_ms` from PortAudio DAC clock; dispatcher opt-in `probe` (poll jitter + send cost); `measure_latency.py` CLI. Probe is None in prod — dispatch path unchanged.
- Callback writes a SINGLE float, no list/lock — deliberate, ISSUE-005 precedent. Sampler thread reads it at 50Hz.
- NEXT: `python -m guitar_helper.measure_latency <track> --seconds 30` on real rig (loopMIDI + Nolly VST2/standalone), then replace hardcoded 75 with a persisted `dispatch_offset_ms`.
- Side-win to weigh at the same time: `poll_interval_s` 50ms→15ms halves link-C jitter, negligible cost.
- Calibrate button / confirm / revert UI (`Phase_4_QOL_changes.md`) deferred to the QOL pass — user's explicit call.

## ISSUE-004 (RESOLVED — 2026-07-15) — VST3 architecture limitation
- **Root cause:** VST3 plugin format doesn't deliver raw MIDI PC to hosted plugins. Steinberg architecture requires host-side parameter mapping via IMidiMapping interface (not a defect in our dispatch code).
- **App code verified correct:** PCs confirmed end-to-end in Cantabile MIDI monitors; dispatch logic proved correct by live test with VST2/standalone.
- **Fix:** Use Nolly **VST2 build** or **standalone app** instead of VST3 for Program-Change-driven tone switching. Both accept raw PC directly and switch presets correctly (confirmed live).
- VST3 remains fine for audio processing and manual preset selection, just not automated PC switching.
- Full diagnosis + Steinberg source citations: `docs/debug/ISSUE-004-nolly-program-change-no-preset-switch.md`

## ISSUE-005 (RESOLVED — 2026-07-13) — pyqtgraph waveform repaint (GIL starvation)
- User observation = natural discriminating test: choppy ONLY while Analysis mode visible (O1 skips playhead repaint when Analysis hidden) → pyqtgraph 20Hz scene repaint starving audio callback of GIL. Confirmed.
- Fix: waveform REMOVED entirely (also user UX decision at post-O2 re-assess). New SegmentTimeline (ui/views/segment_timeline.py): custom QWidget paintEvent, tone-colored rects + playhead, repaints only when playhead crosses a pixel column; click-to-seek; hover tooltip = tone. waveform_view.py + segment_overlay.py DELETED; theme pg helpers (tone_brush/tone_pen/WAVEFORM_*/BAND_ALPHA*) removed, tone_qcolor added. pyqtgraph now unused (spectrum M7 would re-add).
- Also: engine viz_queue=None in Application.attach (no consumer until spectrum; was raising queue.Full every callback).
- O3 boundary dragging will land on SegmentTimeline (drag band edges), superseding old M4 InfiniteLine design (noted in phase4-overhaul-plan.md O3).
- Audible confirmation in Analysis mode: user CONFIRMED clean, 2026-07-13. Restore point: git tag `pre-issue-005-fix` (commit 1330f25).
- Post-O2 user feedback: "Play next" sidebar button works (reorder-after-current, Spotify semantics) but unclear + selection doesn't follow moved item — KEEP, clarity polish deferred to fancy-UI pass.

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
- **Latency:** run the measurement CLI, then land `dispatch_offset_ms` (see section above).
- **Push/merge decision:** O3 ready for main; decide commit/push `phase4-o3-analysis` → main. Latency tooling is uncommitted on top.
- **O4 Output:** preset panel (PC-only) + DispatchLogBuffer/log_sink + dispatch log panel (diagnostic for playback+MIDI).
- **Later pool:** fancy-UI pass (incl. Play-next affordance), cover art/metadata via mutagen (iTunes/Bandcamp tags), lyrics+spectrum (spectrum re-adds pyqtgraph + re-enable viz_queue), queue persistence, more overdrive labels + recalibrate, Phase 5 PyInstaller.
