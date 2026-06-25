# Phase 3 Readiness Plan

_Pre-work to close Phase 2 open loops and de-risk Phase 3. Derived from the SDM review (2026-06-23)._

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
- [ ] Apply EXP-001 #1: `np.mean` → `np.median` in `run_calibrate.py:107`. Regenerate + commit `archetypes.json`. (macro-F1 0.434 → 0.476.)
- [ ] Add low-RMS → `other` gate in `tone_classifier.py` (fixes near-silent → `clean` misfire; 12/14 in EXP-001).
- [ ] Relabel 4 silent `crunch` segments → `other` via correction CLI, then re-calibrate. (Flagged in EXP-001 diagnostic: I_hate_everything 223–229s, carry_on 166–169s + 252–254s, let_it_die 133–135s.)
- [ ] Add a tracked LOOCV macro-F1 check so future feature changes can't silently regress tone accuracy.

### Hygiene / provenance
- [ ] Decide experiments tracking: commit `guitar_helper/experiments/` + `docs/experiments/` + EXP results, or document the exclusion. Don't lose the median decision provenance (currently gitignored).
- [ ] Clean tree: remove/relocate `library_separated.db` + `batch_reanalysis.log`; move `print_db.py` into the package (`tools/` or `guitar_helper/`).
- [ ] Cache raw feature stack in `pipeline.py` (kill the 2× librosa cost: `extract` + `extract_for_classification` both recompute `_stack_raw`).
- [ ] Bump `ruff.toml` target-version to py314 if supported (minor).

### Decision required (owner)
- [ ] Ship Phase 3 on current ~60% accuracy, or label `ambient` (0 labels) + a few more tracks first?
  - Recommended: land the cheap wins above (median + silence gate + relabel), then proceed.
  - **Defer** the MFCC feature-space work (ISSUE-003) — research rabbit hole, not a Phase-3 blocker.

## Exit criteria
- CI green with full Phase 3 dependency set installed.
- `tracks` schema v3 with `source_path`; analysis persists absolute path; a track row can be resolved back to a playable file (with re-hash fallback) independent of any flat `music/` folder.
- Single config object owns library root + resource paths; no entry point relies on CWD-relative defaults for locating audio.
- `archetypes.json` regenerated (median), silence gate in place, silent mislabels fixed.
- Tracked accuracy metric in place; tree clean; experiment provenance resolved.
