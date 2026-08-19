# Save State — Guitar Helper
_Last updated: 2026-08-19_

Status: **Phases 1-3 DONE. Phase 4 M0-M3 + O1-O4 DONE** — the `phase4-overhaul-plan.md` roadmap is complete. 549 tests passing, committed tree ruff-clean. **Gates 1-4, 6 complete; Gate 5 (packaging) only gate left, plus live rig verification**.

## READ FIRST
- **`docs/plans/mvp-implementation-plan.md`** — master execution plan: 6 gates, ordering rationale, how each is accomplished. Everything below is context for it.
- `docs/Report/mvp-readiness-review.md` — findings + quality grades (Reliability C+, Robustness B-, Maintainability A-, SOLID A-, Testability A, Deployability D).
- `docs/Report/Guitar_Performance_Assistant_Report_v0.4.md` — as-built architecture. **v0.3 is superseded** and describes components never built (IRenderer/SpectrumAnalyzer/LyricsParser).
- `docs/architecture/ARCHITECTURE.md` — component map, 17 numbered design decisions (D1-D17), threading contract.

## MVP blockers (remaining)
- **Gate 5 — Packaging.** `pyproject.toml` (fully pinned), PyInstaller spec, app data in `%LOCALAPPDATA%`, bundle archetypes.json, htdemucs_6s, torch+onnxruntime. Clean-machine test + README.

## Gate 3 (ISSUE-007) — RESOLVED
GUI ingestion path built end-to-end: `AnalysisWorker` (QThread), progress dialogs, Home "Add songs…" button + playlist right-click, File menu entry. `delete_track` on `ITrackEditor` role (mixed into `IAppStore`, not `ISegmentStore`); Home right-click "Remove from library" warns with corrected-segment count, caches stem (D5).

## Gate 4 — RESOLVED
**H1:** NullMidiPort fallback + persistent "MIDI disabled" banner (no loopMIDI required). **H2:** `attach()` failures caught broadly, previous track keeps playing. **H3:** correction CLI saves via atomic `apply_edits` (snapshots first). **H5:** playlist creation catches `sqlite3.IntegrityError` only.

## Gate 6 — RESOLVED
README.md, LICENSE (MIT / Aryan Kumar / 2026 — **unconfirmed**, needs deliberate decision before Gate 5 redistributes weights), user-guide.md, Help menu entry.

## Resolved (ISSUE-006/008, B1, schema v10)
- **B1 — `save_segments` not transactional.** RESOLVED: wrapped in transaction; `EditorState.save()` uses atomic `apply_edits()`.
- **ISSUE-006** — preset-map divergence. RESOLVED: option A — `presets.user_modified` column, v10 migration reconciles non-user-modified rows, partial UNIQUE index. Live DB migrated intact.
- **ISSUE-008** — stem-cache poisoning. RESOLVED: atomic publish + completion manifest + validation on hit; sampled digest mandatory.

## Facts NOT derivable from code
- **D3 open / needs live rig test.** "Pause playback while analysing" ships defaulted OFF; by-ear test decides the default. Do not settle from code — ISSUE-005 lesson.
- **LICENSE unconfirmed.** MIT / Aryan Kumar / 2026 picked by default; needs deliberate decision before Gate 5 redistributes Demucs weights (whose licence terms haven't been checked).
- **`run_playback.py` and `measure_latency.py` check `app.midi_available` post-H1**, exit 2 if absent. Matters for `measure_latency`: measuring dispatch latency against a no-op port prints meaningless numbers.
- **MIDI port selection is startup-only.** With the banner, users will expect to fix a missing loopMIDI without relaunching; port picker in Output mode is the fix, not in Wave 2 scope.
- **`Stage` is StrEnum but `Enum.__hash__` hashes the name**, misses on dict lookup by the `.value` string arriving over Qt signal. Key such dicts by `.value`.
- Restore point: `mvp-wave2-restore-point` tag, plus `.backup-mvp-wave2/library.db.bak` (gitignored).

## Product decisions
- **No songs/DB ship.** Library user-supplied; only `archetypes.json` ships.
- **Separation MANDATORY.** Stem first, never analyse full mix. Justification: stem vs full-mix over 123 labelled segments = accuracy .821 vs .463; macro-F1 ~0.69 (stem) vs worse (full-mix). Violations: `pipeline.py:43` defaults to `NullSeparator`, `--no-separate` on CLIs.

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
- Live `library.db`: **9 tracks, 123 segments, 123 corrections, 1 playlist, schema v10.** All rows migrated; no duplicates; unique index on `pc_number >= 0`.
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

