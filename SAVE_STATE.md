# Save State — Guitar Helper
_Last updated: 2026-08-03_

Status: **Phase 1+2+3 DONE. Phase 4 M0+O1+O2+O3+O4 ALL DONE — the milestone roadmap of `phase4-overhaul-plan.md` is complete.** On `main`, 370 tests passing, ruff clean. ISSUE-004/005 RESOLVED. Remaining Phase 4 work is the QOL pass (`docs/new feature specs/Phase_4_QOL_changes.md`), not a milestone.

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
- **Key design:** SegmentLookup snapshots at construction (in-memory bisect, not per-tick DB queries) — this is why segment edits need a song reload to take effect (QOL item). MidiDispatcher own thread, on-change-only dispatch from presets table, lookahead now the persisted `dispatch_offset_ms` (was hardcoded 75 until O4). PlaybackEngine callback strictly non-blocking.
- **MidoPort** opens `'loopMIDI Port 1'` (app SENDS, host receives). App-side dispatch verified correct (PCs logged: carry_on_my_wayward_son shows other@98s, PC0@99.9s, PC4@113s, PC0@124.5s). VST3 limitation discovered; see ISSUE-004 RESOLVED.
- Tests: 133 passing, ruff clean. MidoPort test skipped in CI (`importorskip("rtmidi")`).

## Phase 4 UI M1-M3 (DONE)
- Track dataclass + list_tracks() (LEFT JOIN segments for correction-progress). UI package built: theme/controllers/transport/panels/state/models/main_window/app/run_ui. EditorState (Qt-free) owns load/select/segment_at; Qt-bridge via EditorStateBridge + StateEvent. Tests 152 passing, live verification OK.
## Phase 4 M0 + O1 + O2 (DONE 2026-07-13)
- M0: Application.load split → decode() + attach() (main thread, validate/stop-old/wire-new). LoadWorker(QThread); user errors 1-3 fixed (no freeze/orphans).
- O1: MainWindow shell (sidebar QListWidget + QStackedWidget: Home/Analysis/Media/Output modes) + persistent transport. Analysis mode auto-selected on load; playhead repaint skipped when hidden. QueueState (Qt-free, injectable rng); QueueSidebar (now-playing text, queue list, Shuffle/Repeat/Up/Down/Play-next/Clear). ISSUE-005 FIX: SegmentTimeline replaces pyqtgraph waveform (playhead repaints only per pixel-column, not 20Hz). 
- O2: schema v7→v8 (playlists, playlist_tracks, ON DELETE CASCADE). Home = playlist-first: virtual "Library" + real playlists; double-click song → load+autoplay, queue seeded from shown playlist, STAYS in Home. Shell wiring: _pending_queue after attach; File→Open single-track fallback; track-finished via _on_pos_tick (_was_playing flag + position ≥ duration−50ms). Verified 194 tests, LIVE db migrated v6→v8 OK (9 tracks/129 segments/corrections intact).

## Phase 4 O3 (Analysis) — DONE, committed (see git for the 9-step detail)
- ISP split: `ISegmentStore` (11 methods) → `ITrackCatalog`/`ISegmentReader`/`ISegmentEditor`/`IPresetStore`; deprecated alias kept.
- Schema v7 `segments_calibration` + idempotent `ensure_calibration_copy` (lazy pre-edit snapshot). Merge is physical; originals preserved in the snapshot.
- Qt-free core (`validation.py`, `merge.py`, `editor_state.py`) unit-tested; Qt layer via pytest-qt. Boundary dragging lives on SegmentTimeline (`boundary_at_x` hit-tester is Qt-free).
- SCOPING DECISION (user confirmed): dirty-guard applies only to playlist Analyze/Prev/Next, NOT to O2 queue Prev/Next or Home double-click. Known gap, accepted.
- USER verified live app, no bugs.

## Phase 4 O4 (Output) — DONE 2026-08-03
- **Schema v9**: `settings(key, value)` + `ISettingsStore` role ABC. New `IAppStore = ISegmentStore + ISettingsStore` for the composition root — settings deliberately NOT folded into `ISegmentStore` (doing so broke test_pipeline's MockStore, which is exactly the ISP violation the O3 split exists to prevent).
- **`playback/dispatch_log.py`**: `DispatchEvent` + bounded thread-safe `DispatchLogBuffer` (deque 200). Records EVERY decision — send/hold/unmapped/gap — so a silent amp is distinguishable from a silent dispatcher. `last_send` survives `drain()` so the active-preset indicator is right for a viewer arriving after the switch.
- **MidiDispatcher**: opt-in `log_sink` (None default, same shape as `probe`); `lookahead_ms` now settable; `set_pc_map()` takes a plain dict, never the store — dispatcher thread still never touches SQLite.
- **Logged position = boundary, not cursor** (cursor + lookahead). Deliberate: otherwise every log line reads off-by-the-lookahead vs the segment table.
- **`ui/modes/output.py`** (replaces placeholders.py, deleted): preset table (PC + name editable, validated), per-row **test-send** (ISSUE-004 diagnostic — proves the chain with nothing playing), live log, active-preset indicator, offset spinbox + Apply/Revert.
- `_dispatch_timer` (100ms) runs **only while Output is visible** (O1 precedent); entering Output drains immediately so buffered history shows at once.
- **Latency knob LANDED**: hardcoded 75 → persisted `dispatch_offset_ms` (settings table), applied live to the running dispatcher, survives restart. Revert = one-step undo of the last Apply.
- Live library.db migrated v8→v9 OK (9 tracks / 123 segments / 123 corrections / 1 playlist intact). Backup taken pre-migration.

## Latency calibration — knob DONE 2026-08-03, MEASUREMENT still pending
- Goal: PC lands on the *audible* tone boundary. Full reasoning: `docs/Report/latency-calibration-analysis.md`.
- Model: `optimal_lookahead = L_chain + poll_wait − L_out`. Collapses to ONE global knob — boundaries are seconds apart, so no per-segment correction.
- L_out (tracker lead, est 30-50ms) + hardcoded 75ms lookahead STACK → PCs likely fire **early**, not late. Correction direction is probably *less* lead. "On time by ear" is consistent with slightly-early.
- L_chain (loopMIDI→host→plugin *audible* switch) is NOT measurable from Python. Supplied by ear for now; audio-loopback wizard is the later automation.
- Built: `playback/latency_probe.py` (Samples/Summary/DispatchProbe); engine `last_tracker_lead_ms` from PortAudio DAC clock; dispatcher opt-in `probe` (poll jitter + send cost); `measure_latency.py` CLI. Probe is None in prod — dispatch path unchanged.
- Callback writes a SINGLE float, no list/lock — deliberate, ISSUE-005 precedent. Sampler thread reads it at 50Hz.
- **NEXT (only remaining step): run `python -m guitar_helper.measure_latency <track> --seconds 30` on the real rig** (loopMIDI + Nolly VST2/standalone), then type the result into Output → Dispatch offset. No code change needed any more — O4 made it a persisted knob.
- Side-win still unclaimed: `poll_interval_s` 50ms→15ms halves link-C jitter, negligible cost.
- QOL calibrate *wizard* (confirm popup, auto-measure) still deferred; O4 shipped the manual knob + one-step Revert underneath it.

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
- clean=PC0, edge=PC1, overdrive=PC2, crunch=PC3, metal=PC4, other=-1 (no dispatch).
- CORRECTION (2026-08-03): this line previously read "clean=0, crunch=1, metal=2, edge=3, overdrive=4" — wrong. Verified against live library.db and `_DEFAULT_PRESETS`; the order above is the real clean→metal gain ramp and matches CLAUDE.md.
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
- **Latency:** run the measurement CLI on the rig; dial the number into Output (see section above). Only step left.
- **QOL pass (next real workstream):** `docs/new feature specs/Phase_4_QOL_changes.md`. Biggest-value items by user note: min:sec + typed boundary entry with arrow-key nudge, modified-segment indicator before save, unmerge/split, multi-select, reload-on-save (edits currently need a song switch to take effect — `SegmentLookup` snapshots at construction), icon-based transport, right-click playlist add, preferences tab (count-in / click track), help tab.
- **Later pool:** cover art/metadata via mutagen (iTunes/Bandcamp tags), lyrics+spectrum (spectrum re-adds pyqtgraph + re-enable viz_queue), queue persistence, more overdrive labels + recalibrate, Phase 5 PyInstaller.
