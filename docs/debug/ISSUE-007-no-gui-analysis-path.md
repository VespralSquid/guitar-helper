# ISSUE-007 — The GUI cannot add or analyse songs

**Status:** OPEN (partially addressed). **Blocks MVP release.**
**Date:** 2026-08-04
**Component:** `guitar_helper/ui/` (no owner — the capability does not exist), `guitar_helper/analysis/pipeline.py`
**Found by:** MVP readiness follow-up question — "does the product ship with the database, or is it created as the user adds songs?"
**Plan:** `docs/plans/gui-analysis-pipeline-plan.md`

---

## Summary

The database is created empty on first run and is meant to grow as the user adds
songs. **The GUI provides no way to add one.** Analysis can only be triggered
from the command line, so a packaged `.exe` user has no path from "installed" to
"usable" at all.

This was invisible during development because the developer populates the
library with `run_batch` from a terminal, and every GUI session therefore starts
against an already-populated database.

---

## Product decision (user, 2026-08-04)

- **No songs ship** with the product.
- **No database ships** with the product.
- The library is **entirely user-supplied**.
- Therefore **the GUI must be able to add songs and analyse them.** This is the
  gating requirement for release, not a QOL improvement.

---

## Symptom

On a fresh install the app opens to an empty library with no way forward.
Verified by running `init_db` against an empty directory:

```
db_path exists before? False
db_path exists after?  True
  tracks   : 0
  segments : 0
  playlists: 0
  presets  : [('other',-1),('clean',0),('edge',1),('overdrive',2),('crunch',3),('metal',4)]
```

Every route a user might try is a dead end:

| Route | What actually happens |
|---|---|
| Home → **"Analyze"** button | Misleadingly named. Emits `analyzeRequested` with tracks **already in the database** and enters *correction* mode. Enabled only when `bool(tracks)`, so on a fresh install it is greyed out. It never runs the analysis pipeline. |
| **File → Open…** on a new audio file | `Application.attach()` raises `NoSegmentsError`; the user gets a dialog reading *"No analysed segments … Run run_analysis first."* |
| Home → right-click a song | Only "Add to playlist" / "Remove from this playlist" — both operate on already-analysed tracks. |
| Drag and drop | Not implemented. |

## Root cause

The analysis tier was built CLI-first (correctly — it was the fastest path to a
working pipeline) and the UI work in Phase 4 covered playback, correction and
MIDI output, but never closed the loop back to Tier 1 ingestion.

Confirmed by search: **the entire `guitar_helper/ui/` package contains zero
references to `AnalysisPipeline`, `run_analysis` or `AudioSeparator`.**

```
$ grep -rn "AnalysisPipeline\|run_analysis\|AudioSeparator" guitar_helper/ui/
(no matches)
```

The only callers of `AnalysisPipeline` are `run_analysis.py` and `run_batch.py`,
both `__main__` entry points.

## Why this is a release blocker rather than a gap

`docs/Report/Guitar_Performance_Assistant_Report_v0.4.md` §1 states the
secondary goal as "a polished, distributable desktop application that other
guitarists can install and use." With no ingestion path, an installed app
displays an empty table and instructs the user to run a Python module they have
no interpreter for. Nothing else in the product is reachable — playback,
correction, MIDI dispatch and calibration all require an analysed track.

## Structural obstacle: the pipeline cannot run on a worker thread as written

Analysis is far too slow for the Qt main thread. Measured on
`let_it_die_3DG.wav` (189 s), with the guitar stem already cached:

| Stage | Time |
|---|---|
| SHA-256 hash (44 MB WAV) | 0.05 s |
| `load_mono` (decode + resample) | 6.57 s |
| `extract()` | 1.03 s |
| `extract_for_classification()` | 0.78 s |
| `find_boundaries()` | 2.30 s |
| classify all 13 segments | 0.00 s |
| **Total, separation cached** | **10.74 s** |

**Stem separation is excluded from that figure and dominates it** — htdemucs_6s
is minutes per track on CPU. So a first-time analysis is minutes, not seconds.
Blocking the main thread for that is not an option.

But `AnalysisPipeline.run()` cannot simply be moved to a `QThread`, because it
touches the store at both ends:

```python
duration_ms, file_hash = self._loader.load(path)
if not discard_corrections and any(                       # ← store READ
        s.manually_corrected for s in self._store.get_segments(file_hash)):
    raise ManualCorrectionsExistError(file_hash)
...                                                        # pure computation
self._store.save_track(...)                                # ← store WRITE
self._store.save_segments(file_hash, segments)             # ← store WRITE
```

The sqlite3 connection is created on the main thread and `check_same_thread`
defaults to `True`; the project's threading contract (`ARCHITECTURE.md` §4) makes
the Qt main thread the sole SQLite owner. Handing the connection to a worker
would violate that and invite writer contention.

**This is the same shape as the M0 decode/attach problem, and takes the same
solution** (decision D14): split the pure computation from the persistence, run
the computation on a worker, and do the store access on the main thread.

## Secondary consequences to resolve alongside

1. **Separation stack availability.** `AudioSeparator` lazily imports
   `audio_separator.separator`; on a machine without it (~2 GB of torch +
   onnxruntime, plus ffmpeg, plus a `diffq` stub on Python 3.14) that import
   raises and the analysis fails with a traceback. The GUI needs to detect this
   before starting, not discover it mid-run.
2. **Quality coupling — measured, and now settled as a product rule.** Falling
   back to `NullSeparator` is not a free degradation. Classifying all 123
   human-labelled segments with the shipped stem-calibrated `archetypes.json`:
   **accuracy 0.821 → 0.463, macro-F1 0.783 → 0.324**, with `crunch` F1
   collapsing 0.89 → 0.13 (27 of 29 crunch segments predicted `metal`) and
   `overdrive` to 0.00. The full mix collapses nearly everything into `metal`,
   because drums and bass raise flatness/ZCR/centroid — the same signature as
   distortion.

   **Methodology note:** these are *resubstitution* scores — the same
   labelled segments that built `archetypes.json` are being classified by
   it, so both numbers are optimistically biased and neither is the
   model's true generalisation performance (LOOCV puts that at macro-F1
   ~0.69, see `docs/experiments/EXP-001-results.md`). The *comparison* is
   still valid because both sides share the method and the only variable
   is stem versus full mix. Do not quote 0.783 as the classifier's
   accuracy.

   **User decision 2026-08-04: the full mix is never analysed.** Every song is
   stemmed first, then analysed. This makes separation a hard dependency rather
   than a recommended default, and exposes two existing violations:
   `AnalysisPipeline` *defaults* to `NullSeparator` when no separator is
   injected (`pipeline.py:43`), and `--no-separate` is a user-facing flag on
   both CLIs. "Start over on a song" is scoped as remove-and-re-add
   (user, 2026-08-04) rather than a force-reseparate flag — which in turn
   needs `delete_track`, a capability that also does not exist. See the
   plan §0.
3. **Interrupted separation permanently poisons the stem cache.**
   `separate_guitar` treats any existing `<hash>_guitar.wav` as valid. A stem
   truncated to 33% is returned as a cache hit and loads silently as 87.9 s of a
   264 s track, producing a plausible but wrong segment list that re-analysis
   cannot repair. Needs atomic writes plus duration validation — see the plan
   §2.3.
4. **Separation model weights are downloaded on first use.** They are not in the
   repo and default to a temp directory, so the "fully offline" claim in
   `CLAUDE.md` does not hold for a fresh machine's first analysis unless the
   weights are bundled.
5. **ffmpeg.** The stated primary target is iTunes `.m4a`, which `pydub`
   decodes via ffmpeg. Without ffmpeg on PATH the user's main format fails.
6. **Interaction with ISSUE-006 and the `save_segments` defect.** GUI analysis
   makes `save_segments` reachable by ordinary users for the first time. On a
   database missing the `edge` preset row (ISSUE-006) an `edge` segment raises
   `IntegrityError`, and because `save_segments` is not transactional
   (`mvp-readiness-review.md` §B1) that failure destroys the track's existing
   segments. **ISSUE-006 and B1 must be fixed before GUI analysis ships**, or
   this issue turns a latent defect into a routine one.

## Current status

**Groundwork landed in commit `922bf00` (Gate 3 Wave 1).** The Qt-free tier split is in place: `analyse()` returns pure `AnalysisResult`, `persist()` handles store writes on the main thread, `run()` wraps both and maintains CLI compatibility. `AnalysisPipeline` now requires `separator` as a keyword-only argument (the invariant enforced); `--no-separate` removed; `NullSeparator` documented as test-only. `analysis/environment.py` added for preflight (stack importability, weights, ffmpeg, paths).

**GUI analysis pipeline still absent.** The "Analyze" button misnames and does not add new songs. No `AnalysisWorker`, progress dialog, or Home wiring. The `delete_track` capability does not exist, so users cannot remove songs. Removing-and-re-adding requires that deletion work first. This is Wave 2 of the Gate 3 implementation and is the remaining blocker for release.

## Lessons

- **A CLI-first tier needs an explicit "close the loop" milestone.** Every
  Phase 4 milestone assumed an analysed library as its starting condition, so no
  milestone ever owned the transition from empty to populated.
- **Developer workflow masks first-run defects.** The library was never empty on
  a developer machine after Phase 1. A first-run check against a scratch
  directory belongs in the release checklist.
- **"Can a new user reach the second screen?" is a different question from "do
  the features work?"** The review that preceded this found ten real defects
  without noticing that the product had no entry point, because it evaluated
  components rather than the path a new user takes through them.
