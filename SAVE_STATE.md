# Save State — Guitar Helper
_Last updated: 2026-08-04_

Status: **Phases 1-3 DONE. Phase 4 M0-M3 + O1-O4 DONE** — the `phase4-overhaul-plan.md` roadmap is complete. 370 tests passing, committed tree ruff-clean. Work has shifted from features to **MVP release readiness**.

## READ FIRST
- **`docs/plans/mvp-implementation-plan.md`** — master execution plan: 6 gates, ordering rationale, how each is accomplished. Everything below is context for it.
- `docs/Report/mvp-readiness-review.md` — findings + quality grades (Reliability C+, Robustness B-, Maintainability A-, SOLID A-, Testability A, Deployability D).
- `docs/Report/Guitar_Performance_Assistant_Report_v0.4.md` — as-built architecture. **v0.3 is superseded** and describes components never built (IRenderer/SpectrumAnalyzer/LyricsParser).
- `docs/architecture/ARCHITECTURE.md` — component map, 17 numbered design decisions (D1-D17), threading contract.

## MVP blockers (2026-08-04 review)
- **B1 — `save_segments` is not transactional.** DELETE + executemany + commit; a mid-write failure leaves the delete + partial insert in an OPEN transaction, committed by the next unrelated write. REPRODUCED destroying a `manually_corrected` segment. Same class as the June wipe; the `ManualCorrectionsExistError` guard does NOT cover it. `EditorState.save()` has the same shape.
- **ISSUE-006** — migrations never converge on `_DEFAULT_PRESETS` (`_seed()` runs only when `schema_version` is absent; 3 of 5 preset changes shipped with no migration). A v6-era DB keeps clean0/crunch1/metal2/edge3/overdrive4 = wrong PC for every tone but clean, silent, audible only by ear. A v1-era DB has NO `edge` row -> FK violation -> triggers B1. No UNIQUE on `pc_number`.
- **ISSUE-007** — the GUI cannot add or analyse songs. `grep AnalysisPipeline guitar_helper/ui/` = zero matches. A packaged .exe opens to an empty library and tells the user to run a Python module. The Home "Analyze" button is misnamed (it opens ALREADY-analysed tracks for correction).
- **ISSUE-008** — stem-cache validity is `cached.exists()` only. A stem truncated to 33% returns as a valid hit and loads as 87.9s of a 264s track. 13 failure cases in 4 classes. A zero-filled same-size stem PASSES a duration check and makes every segment `other` = NO MIDI dispatch at all.
- **Packaging: nothing exists.** No pyproject/README/LICENSE/PyInstaller spec. `ui/state/` is an implicit namespace package (PyInstaller misses these). db/stems/archetypes resolve to CWD.

## Product decisions (2026-08-04)
- **No songs and no database ship.** Library entirely user-supplied. Only `archetypes.json` ships.
- **Separation is MANDATORY — the full mix is NEVER analysed.** Stem first, analyse the stem only. Justification (resubstitution scores, so biased, but the comparison is valid): stem vs full-mix over the 123 labelled segments = accuracy .821 vs .463; crunch F1 .89 vs .13 (27/29 crunch -> metal); overdrive .62 vs .00. Honest generalisation figure remains the EXP-001 LOOCV macro-F1 ~0.69.
- Violations to fix: `pipeline.py:43` defaults to `NullSeparator` (silent full-mix analysis; 8 of 9 test call sites rely on that default); `--no-separate` on both CLIs.
- **"Start over on a song" = remove from library + re-add.** No force-reseparate flag. But `delete_track` DOES NOT EXIST — must be built. `playlist_tracks.file_hash` has NO ON DELETE CASCADE, so a bare DELETE FROM tracks FK-errors.
- Removing a song **warns with the corrected-segment count, then deletes**; transactional. Cached stem is KEPT so re-add is fast.
- App data -> `%LOCALAPPDATA%\GuitarHelper`, seeded on first run. Bundle the torch/onnxruntime stack AND the htdemucs weights.
- **OPEN — needed before Gate 1:** ISSUE-006 reconcile strategy A (`presets.user_modified` flag + Output divergence banner, recommended) / B (legacy fingerprint) / C (detect-only).

## Two models — do not conflate
- `archetypes.json` = **3 KB**, 5 tones x 24 floats, OURS, adapts via `run_calibrate`, ships in git.
- `htdemucs_6s` = **52 MB** third-party pretrained SEPARATION net, fixed, never calibrated, NOT in the repo. Currently downloaded to `C:/tmp/audio-separator-models/` — a temp dir, unsafe.
- `archetypes.json` is useless without htdemucs running first (it is calibrated on stem features). The "~2 GB" figure is torch+onnxruntime (the code that runs the net), not the weights.

## CRITICAL incident — calibration labels lost & protected
- The original 129 manual labels were WIPED by a re-analysis (2026-06-19 reset `manually_corrected`->0). Unrecoverable: OneDrive version history had only post-wipe copies; `*.db` is gitignored. Only `archetypes.json` survived (in git).
- Root-cause fix in place: re-analysis raises `ManualCorrectionsExistError` unless `--discard-corrections`; `run_batch` reports SKIP. **B1 above is the remaining hole in this protection.**

## Current calibration state
- `archetypes.json`: clean, edge, overdrive, crunch, metal (mean-based). `ambient` removed.
- EXP-001 LOOCV macro-F1 ~0.69 (was 0.434). Per-tone F1: metal .89, crunch .82, overdrive .71, other .69, clean .56, edge .48.
- `overdrive` has only 5 labelled segments — least robust; label more + re-run `run_calibrate`.
- Live `library.db`: **9 tracks, 123 segments, 123 corrections, 1 playlist, schema v9.**
- Calibration statistic = **mean** (EXP-001: clean-data LOOCV prefers mean .693 vs median .660). Median is the fallback if outlier contamination returns.

## MIDI preset mapping (presets table = source of truth; never hardcode)
- FRESH DB / `_DEFAULT_PRESETS`: clean=PC0, edge=PC1, overdrive=PC2, crunch=PC3, metal=PC4, other=-1 (no dispatch).
- **RETRACTION (2026-08-04) of the 2026-08-03 "CORRECTION".** That note called clean0/crunch1/metal2/edge3/overdrive4 wrong. It was NOT wrong — it is the real layout of any DB created before the gain-ramp reorder (ISSUE-006). Both lines were true, of different databases; the 08-03 check was made against a fresh DB and over-generalised.
- The live DB reads the fresh order despite having been migrated v6->v8->v9, so those rows were changed after migration — almost certainly via the O4 preset table (the only caller of `save_preset`). Do NOT assume other DBs match.

## Resolved issues (detail in docs/debug/)
- **ISSUE-004** VST3 does not deliver raw Program Change to hosted plugins (Steinberg architecture, not our bug). **Use Nolly VST2 or standalone.** Deployment constraint — must be in the README.
- **ISSUE-005** pyqtgraph 20Hz scene repaint starved the audio callback of the GIL. Waveform deleted; `SegmentTimeline` repaints only per pixel-column. pyqtgraph now UNUSED (still in requirements — drop it). Restore point: tag `pre-issue-005-fix`.
- ISSUE-001 (auto-k), ISSUE-002 (dual normalisation), ISSUE-003 (edge/crunch overlap -> stem separation).

## Latency — knob DONE, measurement still pending
- Model: `optimal_lookahead = L_chain + poll_wait - L_out`. ONE global knob; boundaries are seconds apart.
- `L_out` (tracker lead) + the 75ms lookahead STACK, so PCs likely fire EARLY, not late. The correction is probably *less* lead.
- `L_chain` (loopMIDI->host->plugin audible switch) is NOT measurable from Python — supplied by ear.
- NEXT: run `python -m guitar_helper.measure_latency <track> --seconds 30` on the rig, type the result into Output -> Dispatch offset. **The `settings` table is EMPTY — the knob has never been set.** NOT a release blocker (75ms default works and is user-tunable).
- Unclaimed side-win: `poll_interval_s` 50ms -> 15ms halves poll jitter, negligible cost.

## Environment
- Python 3.14.3, Windows 11; venv `.venv/`. ffmpeg 8.0.1 installed. Primary target format: iTunes .m4a.
- `audio-separator` 0.44.2 (htdemucs_6s), separate heavy install — see `requirements-separation.txt` for the py3.14 `--no-deps` + `diffq` stub procedure.
- `python-rtmidi`: no cp314 wheel -> meson source build; runtime-only, not in CI. **The frozen build must not need to compile it.**
- `ruff.toml` target-version=py312 (py314 bump deferred). CI: windows-latest, py3.14, ruff + pytest.
- **The repo is inside OneDrive.** `stems/` (400 MB) syncs pointlessly, and Files On-Demand can dehydrate a stem so it `exists()` with the right size but needs a network fetch. Move the dev stems dir out. Volume is 95% full (54 GB free).
- Measured: analysis of a 189s track = 10.7s with the stem cached; separation itself is minutes and dominates. The `_stack_raw` duplication is 0.77s of that (7%) — NOT the "~2x" an earlier draft claimed.

## Deferred / out of MVP scope
- QOL pass (`docs/new feature specs/Phase_4_QOL_changes.md`) — min:sec + typed boundary entry, modified-segment indicator, unmerge/split, multi-select, reload-on-save (edits need a song switch today: `SegmentLookup` snapshots at construction, D6), icon transport, right-click playlist add, preferences tab, help tab.
- Review findings M1-M7 · `MainWindow` SRP extraction (441 lines) · move `ui/editor/` to fix the Tier1->Tier3 import · stem-cache eviction · lyrics (`guitar_helper/lyrics/` is an EMPTY package) · spectrum (re-adds pyqtgraph + viz_queue) · cover art · queue persistence · `ambient` re-add · more overdrive labels.
- Hygiene: 5 stray `.db` files at root, `batch_reanalysis.log`, `print_db.py`.
- Accepted gap (user-confirmed): the dirty-guard applies only to playlist Analyze/Prev/Next, not to queue Prev/Next or Home double-click.

## Uncommitted
- `guitar_helper/playback/audio_buffer.py:19` — a `#sample rate` comment with trailing whitespace. W291; committing it turns CI red. The committed tree is clean.
