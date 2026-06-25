# EXP-001 — Mean vs Median Tone Archetype Calibration

**Status:** Designed, not yet run
**Date:** 2026-06-19
**Owner:** parallel investigation agent
**Related:** ISSUE-003 (edge/crunch feature overlap), run_calibrate.py, tone_classifier.py

---

## 1. Motivation

The library's segments are now manually corrected, giving ground-truth tone labels. Three
known facts make robust statistics worth testing:

- **Near-silent / transient frame contamination** (ISSUE-003 D): outro frames measure
  rms≈0.000 with flatness≈0.728 / zcr≈0.996 — a noise-floor artifact that drags a segment's
  *mean* feature vector toward crunch/metal. A per-frame **median** would suppress these.
- **Single-song outliers across a tone**: one atypically bright/fizzy song's segment can pull
  a tone's *cross-segment mean* archetype off-center.
- **edge/crunch overlap** (ISSUE-003): the two tones sit ~0.04 apart in the 5 active features
  — a *separability* problem that robust central tendency may NOT fix.

Goal: decide with evidence whether to switch the calibration central-tendency statistic from
mean to median, and at which aggregation level — and to record honestly if it makes no
meaningful difference.

## 2. The two aggregation levels (both are mean→median candidates)

| Level | Where (current) | Constraint |
|---|---|---|
| **Frame reduction** (within a segment) | `run_calibrate.py:87` and `pipeline.py:59` — `clf_matrix[:, f0:f1].mean(axis=1)` | **Consistency invariant:** the stat used to build an archetype MUST equal the stat used to reduce the segment being classified. Change requires editing BOTH files. |
| **Cross-segment archetype** | `run_calibrate.py:105` — `np.mean(vecs, axis=0)` | Independent — runtime never aggregates across segments, so median here is unconstrained. |

## 3. Hypotheses

- **H1** — within-segment median reduces noise-floor/transient frame contamination →
  higher accuracy, especially on near-silent and high-transient segments.
- **H2** — cross-segment median reduces single-song outlier contamination → better
  cross-song generalization.
- **H3 (null / critical)** — at n≈8–12 segments/tone, mean≈median; the difference is within
  noise and edge/crunch overlap persists because separability is feature-space-limited, not
  central-tendency-limited. **This is an acceptable, useful outcome.**

## 4. Experimental design

### Factors
- **A — frame-reduction stat:** {mean, median} (applied to BOTH calibration archetype build
  AND the classified held-out vector — enforce the consistency invariant in code).
- **B — cross-segment stat:** {mean, median}.
- **C — distance metric:** {L2 (Euclidean), L1 (Manhattan)}. Rationale: mean is the
  L2-optimal center, coordinate-wise median is the L1-optimal center — pairing matters.

Full grid = 2×2×2 = **8 conditions**. Plus baseline **B0** = current production
(uncalibrated `DEFAULT_ARCHETYPES`, mean frame reduction, L2). Note: condition
(mean, mean, L2) = current calibration behaviour.

### Evaluation protocol — leave-one-out cross-validation (MANDATORY)

Calibrating and testing on the same segments leaks data and biases the result toward the
mean (under L2 the mean fits its own members by construction). Use **LOOCV**:

> For each labeled segment `s` of tone `t`: build archetypes from all OTHER labeled segments
> (grouped by tone), classify `s`, record predicted vs ground-truth. Cheap at this scale.

- Tones reduced to <2 members after the hold-out → fall back to `DEFAULT_ARCHETYPES` for that
  tone and flag it in the output.
- `other`-labeled segments: excluded from archetype building. Report metrics **both** with
  `other` included (does the confidence floor route it correctly?) and excluded.

### Metrics (per condition)
- Overall accuracy; macro and per-tone precision / recall / F1.
- Full confusion matrix (6×6 incl. `other`).
- **edge↔crunch confusion rate** — primary ISSUE-003 success metric.
- Confidence: mean confidence on correct vs incorrect predictions; best-vs-worst archetype
  distance margin (does the statistic widen separation?).
- Archetype separation table: pairwise archetype distances, especially **edge–crunch**
  (larger = more separable).

## 5. Outlier diagnostic (run first, report separately)

- Per tone, per feature (5 active features + report all 13 MFCCs): mean, median, std, IQR,
  min, max.
- Flag any segment outside 1.5×IQR or >2σ on any active feature. For each flag, print:
  song filename, segment time range, offending feature(s) — so the user can judge
  **mislabel vs stem artifact vs genuine tonal variety**.
- Per labeled segment, report the fraction of near-silent frames (rms below a small
  threshold) — quantifies the ISSUE-003-D contamination and the expected payoff of the
  frame-level median.

## 6. Implementation tasks (for the executing agent)

1. **Evaluation harness** — new module `guitar_helper/experiments/calibration_eval.py`
   (or `scripts/`). Parameterizes factors A/B/C, runs the LOOCV loop, writes per-condition
   metrics + confusion matrices + archetype-separation tables to `results/exp-001/`
   (gitignored) as CSV/JSON plus a markdown summary.
2. **Outlier diagnostic** — same module behind a `--diagnose` flag (Section 5 output).
3. **Deterministic unit tests** (CI-safe, synthetic vectors, NO audio): mean-vs-median
   archetype correctness; classifier picks nearest archetype under both L2 and L1.
4. **Findings** — append a results section to ISSUE-003 or create
   `EXP-001-results.md` with the recommendation.

**Do NOT** modify `pipeline.py`, `run_calibrate.py`, or `tone_classifier.DEFAULT_ARCHETYPES`,
and do NOT write to `archetypes.json` or the `segments` table. The experiment is read-only.

## 7. Reuse map (existing code — do not reinvent)

- `AudioLoader.load_mono` — `guitar_helper/analysis/audio_loader.py`
- `FeatureExtractor.extract_for_classification` — `feature_extractor.py:74`
- `_find_audio`, `_stems_path`, frame-slice math — `run_calibrate.py:31-49`, `:85-87`
- DB query for `manually_corrected=1` segments — `run_calibrate.py:121-127`
- `ThresholdClassifier.classify` confidence-floor logic + `TONE_LABELS`, `_CONFIDENCE_FLOOR`
  — `tone_classifier.py:11-12`, `:74-85` — parameterize the distance metric rather than
  reusing the L2-hardcoded version as-is.

## 8. Constraints & invariants

- **Consistency invariant** (Section 2) — enforce in the harness; flag loudly because the
  eventual production fix must edit both `pipeline.py:59` and `run_calibrate.py:87`.
- **Read-only** on production artifacts (DB segments, `archetypes.json`).
- Fully offline; stems cached at `stems/<hash>_guitar.wav`, fallback to `music/`.
  ffmpeg installed.
- **CI:** the harness needs audio → excluded from CI. Only the synthetic unit tests (task 3)
  run in CI. Branch = `calibration-stat-experiments`; `results/` gitignored; any candidate
  archetypes stamped with `{stat_frame, stat_cross, metric, n_segments, segment-set hash}`
  for reproducibility.

## 9. Decision criteria

- **Primary:** highest LOOCV macro-F1 with the lowest edge↔crunch confusion.
- **Tie-break:** larger edge–crunch archetype separation; wider correct-vs-incorrect
  confidence margin.
- **Null result is valid:** if all conditions cluster within ~1–2 segments' worth of accuracy
  AND edge/crunch confusion stays high, conclude "central tendency is not the lever" and
  redirect to ISSUE-003 fix B (activate/weight the MFCCs) or add a distortion-sensitive
  feature.

## 10. Optional stretch
- Add a **trimmed mean** (drop top/bottom ~15%) and **geometric median** as extra A/B levels
  if the median shows promise — middle grounds between mean and coordinate-wise median.
