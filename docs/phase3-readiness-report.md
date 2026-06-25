# Phase 3 Readiness — Completion Report

_Status: complete (2026-06-24). Work committed on branch `phase3-readiness` (`0922b7c`), not yet pushed. 106 tests passing, ruff clean. Companion to `phase3-readiness-plan.md`._

## Purpose
Close Phase 2 open loops and de-risk Phase 3 before any MIDI code is written: CI dependency gate, file locator + config, classifier accuracy, calibration data, experiment provenance. The plan's premise held — wrong tone labels are audible once Phase 3 dispatches presets live, and playback can't open a file the DB can't locate, so both landed first.

---

## What was done, and why

### 1. CI dependency gate
- **Change:** moved `python-rtmidi` to a new `requirements-runtime.txt`; CI installs only `requirements.txt`.
- **Why:** `python-rtmidi` has no cp314 wheel (any version → meson source build), which would make CI slow/fragile. Tests use `MockMidiPort` and never need it; `MidoPort` uses `mido` (pure Python, kept) and only needs the rtmidi backend at actual port-open time. sounddevice/PySide6/pyqtgraph all resolve from wheels, so they stayed. This unblocks adding MIDI code without a source-build risk in CI.

### 2. File locator + config (the playback/import blocker)
- **Migration mechanism refactor:** replaced the `elif row[0] < 2` ladder with a cumulative version loop (`_apply_migrations` + `_MIGRATIONS`). **Why:** the old ladder only knew how to reach v2; adding v3+ under more `elif`s would silently skip steps. The loop composes every pending migration in order — a prerequisite for everything below.
- **Schema v3 — `tracks.source_path`:** pipeline now persists `str(path.resolve())`; basename kept as `filename` for display. `save_track` changed from `INSERT OR IGNORE` to upsert. **Why:** playback needs a real path to open; the old schema recorded none. Upsert fixes a second bug — a moved file's path would never refresh because `INSERT OR IGNORE` no-ops on an existing hash. `calibration_excluded` is preserved across the upsert.
- **Relocation fallback — `analysis/file_locator.py:locate()`:** stored path first, else re-hash candidates under the library root. `AudioLoader.hash_file` made public (hash-only, no decode). **Why:** the old `run_calibrate._find_audio` did `music_dir/filename`, which breaks on multi-folder libraries and silently grabs the wrong file on basename collisions. Content hash is the only collision-proof identity.
- **`config.py:AppConfig`:** one object owns root/db/library_root/stems/archetypes/model_dir, all absolute, derived from a single `root` (explicit > `GUITAR_HELPER_HOME` > CWD). All four CLIs share `add_config_args`/`config_from_args`; they inject `ThresholdClassifier(calibration_path=cfg.archetypes_path)`. **Why:** the same CWD-relative defaults were duplicated across four entry points and baked into the classifier — five places that could drift. Absolute paths also free playback/import from depending on the process CWD. Backward-compatible (CWD default).

### 3. Classifier accuracy
- **Low-RMS → `other` silence gate** in `tone_classifier.py` (`rms_floor`, default 0.02 in clf-normalized space). **Why:** near-silent stems read as low energy regardless of tone and were misclassifying as `clean`; an explicit gate is the correct, direct fix.
- **Calibration statistic — settled on mean.** EXP-001 (on the original data) recommended cross-segment median because a few silent segments mislabeled `crunch` inflated the mean. After re-labeling clean data (those silences now correctly `other`), the LOOCV grid reversed: **mean 0.693 vs median 0.660 macro-F1**. Reverted `run_calibrate` to `np.mean`; median documented as the fallback if future labels reintroduce outliers.
- **Tracked accuracy guardrail:** the EXP-001 harness and its CI-safe synthetic primitive tests (`tests/test_calibration_eval.py`) are now version-controlled and run in CI. Real-audio LOOCV stays a local/manual check (no audio in CI by design); the current baseline (~0.69) is recorded in the EXP-001 results.

### 4. Tone preset model changes (user decisions during the phase)
- **`ambient` removed** (schema v4) — 0 calibration data, deferred. FK-safe migration (deletes the preset only if unreferenced).
- **`edge` moved PC4 → PC3** (schema v5) so PCs are contiguous.
- **`overdrive` added at PC4** (schema v6) — a mid-gain tone between `edge` and `crunch`, with an interpolated default archetype until calibrated. **Why:** some segments fit neither edge nor crunch; the new class absorbs that mid-gain region.

### 5. Provenance / hygiene
- Experiments un-gitignored: `guitar_helper/experiments/`, `docs/experiments/`, the synthetic test — so the calibration decision history is tracked. Raw harness output (`results/`) and local DB backups (`library.db.bak-*`) stay ignored.

---

## Unplanned event: calibration label loss (and the fix)

Partway through, the 129 manually-corrected segments behind EXP-001 and `archetypes.json` were found **wiped from `library.db`** — a re-analysis on 2026-06-19 had reset `manually_corrected` to 0 (the "re-analysis replaces all segments" behavior noted in the project docs). Only `archetypes.json` survived (committed in git; `*.db` is gitignored, so no DB version had the labels).

- **Recovery attempt:** OneDrive version history was tried several times — all available versions were post-wipe. Unrecoverable.
- **Root-cause fix (now in place):** re-analysis refuses to overwrite a track that has manual corrections, raising `ManualCorrectionsExistError` unless `--discard-corrections` is passed explicitly. `run_batch` reports such tracks as `SKIP`. This makes silent label loss impossible going forward.
- **Recovery path taken:** re-analyzed all 9 songs fresh (cached stems, silence gate active), then re-labeled. All 129 segments are now verified ground truth (`manually_corrected=1`); calibration rebuilt across all 5 tones.

**Net accuracy outcome:** macro-F1 **0.434 → ~0.69**; edge/crunch confusion **0.150 → 0.000** (ISSUE-003 effectively resolved — `overdrive` absorbs the mid-gain overlap). Clean↔edge is the new, smaller ceiling.

---

## Exit criteria — status
- ✅ CI green without the source-build dependency.
- ✅ `tracks` schema (now v6) with `source_path`; analysis persists absolute path; tracks resolvable to disk by hash.
- ✅ Single config object; no entry point relies on CWD-relative defaults.
- ✅ `archetypes.json` recalibrated (mean — corrected vs the plan's "median"); silence gate in place; silent mislabels fixed (re-labeled).
- ✅ Tracked accuracy guardrail; experiment provenance committed.

## Deferred (not blockers for Phase 3)
- **Tree/perf hygiene:** remove/relocate `library_separated.db` + `batch_reanalysis.log`; move `print_db.py` into the package; cache the 2× librosa feature stack in `pipeline.py`; bump `ruff.toml` to py314.
- **`overdrive` is thin** (5 labelled segments) — least robust archetype; label more and re-run `run_calibrate` to sharpen.
- **MFCC feature-space work (ISSUE-003 deep fix):** unnecessary — clean labels + `overdrive` already drove edge/crunch confusion to 0.
- **QOL interactive segment editor** (`docs/new feature specs/Phase_3_QOL_Segmenting.md`): pinned to **Phase 4** — it depends on `PlaybackEngine` (Phase 3) + `WaveformView`/`SegmentOverlay` (Phase 4); building it now would mean throwaway scaffolding. Only the cheap "merge consecutive same-tone segments" could be pulled forward, and it has no runtime value (dispatch already fires only on tone change).
