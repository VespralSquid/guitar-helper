# Phase 3 Readiness Plan

_Pre-work to close Phase 2 open loops and de-risk Phase 3. Derived from the SDM review (2026-06-23)._

> **COMPLETE (2026-06-24).** See `phase3-readiness-report.md` for the full write-up incl. the
> calibration-label-loss incident and the mean-vs-median reversal. Committed on branch
> `phase3-readiness`. Checkboxes below reflect final state.

## Goal
Close documented open loops (file locator, classifier accuracy, calibration data, CI deps, tree hygiene) before any Phase 3 MIDI code is written. Phase 3 dispatches amp presets live — wrong tone labels are audible, so accuracy fixes land here first; and playback can't open a file the DB can't locate, so the file-locator gap lands here too.

## Tasks

### Gate (do first)
- [x] **Confirm CI green with Phase 3 deps.** Resolved: `python-rtmidi` (no cp314 wheel, any version → meson source build) moved to `requirements-runtime.txt`, out of CI's install path. Tests use `MockMidiPort`; `MidoPort` uses `mido` (pure Python, kept) and only needs the backend at port-open time. sounddevice/PySide6/pyqtgraph all resolve from wheels. CI install + ruff + 93 tests green locally on py3.14.

### File locator + config (import / playback blocker)
_Today `tracks` stores only `file_hash` + bare `filename` (`pipeline.py:71` saves `path.name`); no directory is recorded. The only file→disk mapping is `run_calibrate._find_audio`, which assumes one flat `music/` folder — breaks for multi-folder/playlist import, and gives Phase 3 playback nothing to open._
- [x] **Schema v2→v3: add `source_path` (absolute) to `tracks`.** Done. Also refactored the migration mechanism from an `elif` ladder into a cumulative version loop (`_apply_migrations` + `_MIGRATIONS` dict) so multi-step upgrades compose; `_CURRENT_VERSION = 3`.
- [x] **Persist the full path on analysis.** Done. `save_track` (ABC + SQLite + MockStore) takes `source_path`; pipeline passes `str(path.resolve())`, keeps basename as `filename`. `INSERT OR IGNORE` → upsert so re-analysis of a moved file refreshes the path (preserves `calibration_excluded`).
- [x] **Add a relocation fallback.** Done. New `analysis/file_locator.py:locate()` — stored path first, else re-hash candidates under the library root. `AudioLoader.hash_file` now public (hash-only, no decode). Replaces `run_calibrate._find_audio` brittle `music_dir/filename` lookup; new `--library-root` arg.
- [x] **Introduce one config object.** Done. `guitar_helper/config.py:AppConfig` owns root/db/library_root/stems/archetypes/model_dir, all resolved absolute from one `root` (explicit > `GUITAR_HELPER_HOME` > CWD). Shared `add_config_args`/`config_from_args` replace the per-CLI defaults across all 4 entry points; CLIs inject `ThresholdClassifier(calibration_path=cfg.archetypes_path)`. `run_batch` resolves a relative folder under `library_root`. Backward-compatible (CWD default). +6 tests, 99 passing.

### Classifier accuracy (cheap, documented wins)
- [x] **Calibration statistic — settled on mean, not median.** The 129 EXP-001 labels were lost (re-analysis wipe; unrecoverable) and the library was re-labeled from clean data. On clean data the LOOCV reversed: mean 0.693 > median 0.660 (the median win was an artifact of silent-`crunch` mislabels, now fixed). `run_calibrate` uses `np.mean`; median documented as the fallback.
- [x] **Low-RMS → `other` gate added** in `tone_classifier.py` (`rms_floor`).
- [x] **Silent mislabels fixed** — superseded by the full re-label: all 9 songs re-analyzed + re-labeled; silent regions now correctly `other`. (Original EXP-001 timestamps were stale post-reanalysis.)
- [x] **Tracked LOOCV guardrail** — EXP-001 harness + CI-safe synthetic tests now committed; real-audio baseline (~0.69 macro-F1) recorded in `docs/experiments/EXP-001-results.md`.

### Hygiene / provenance
- [x] **Experiments tracked** — `guitar_helper/experiments/` + `docs/experiments/` + synthetic test un-gitignored and committed; raw `results/` stays ignored.
- [ ] _DEFERRED_ — clean tree: remove/relocate `library_separated.db` + `batch_reanalysis.log`; move `print_db.py` into the package.
- [ ] _DEFERRED_ — cache raw feature stack in `pipeline.py` (2× librosa cost).
- [ ] _DEFERRED_ — bump `ruff.toml` target-version to py314.

### Decision required (owner) — RESOLVED
- [x] Landed the cheap wins (mean calibration + silence gate + full re-label) and proceeded. `ambient` removed (deferred); `overdrive` added as a new mid-gain tone. MFCC feature-space work (ISSUE-003) unneeded — edge/crunch confusion already at 0.

## Exit criteria — met
- ✅ CI green without the source-build dep.
- ✅ `tracks` schema (now v6) with `source_path`; analysis persists absolute path; resolvable to disk by hash.
- ✅ Single config object; no CWD-relative defaults for locating audio.
- ✅ `archetypes.json` recalibrated (mean), silence gate in place, silent mislabels fixed.
- ✅ Tracked accuracy guardrail; experiment provenance committed. (Tree/perf hygiene deferred — non-blocking.)
