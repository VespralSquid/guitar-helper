# EXP-001 Results — Mean vs Median Tone Archetype Calibration

**Status:** Complete
**Date:** 2026-06-19
**Spec:** [EXP-001-mean-vs-median-calibration.md](EXP-001-mean-vs-median-calibration.md)
**Harness:** `guitar_helper/experiments/calibration_eval.py` (read-only; touches no production code)
**Raw output:** `results/exp-001/` (gitignored) — `summary.md`, `diagnostic.md`, `conditions.json`

---

## TL;DR

- **Median helps — meaningfully.** Best condition (full median + L1) lifts LOOCV macro-F1
  from **0.434 → 0.495** (+14% relative) and no-`other` accuracy from **0.522 → 0.626**.
- **The lever is the cross-segment archetype, not the per-frame reduction.** Every
  cross-segment-median condition beat every cross-segment-mean condition.
- **The mechanism is exactly the one predicted (ISSUE-003 D):** a few near-silent segments
  inflate the *mean* crunch archetype's flatness 4.6× (0.046 vs 0.010 median). Median ignores
  them. crunch F1 jumps **0.474 → 0.635**.
- **It does NOT fix edge/crunch overlap.** edge↔crunch confusion barely moved
  (0.150 → 0.138); edge also bleeds heavily into clean. Separability remains a feature-space
  problem (dormant MFCCs) — confirming hypothesis H3 in part.
- **Bonus finding:** several "crunch" labels sit on 100%-silent regions — likely mislabels.

## Data

129 manually-corrected segments across 9 tracks. Per-tone: crunch 46, edge 34, metal 23,
clean 12, other 14, **ambient 0** (no labels → always falls back to default archetype).
Evaluation = **leave-one-out cross-validation** (no train/test leakage).

## Full grid (sorted by macro-F1)

| condition | acc (all) | acc (no-other) | macro-F1 | edge/crunch conf | conf ok/err |
|---|---|---|---|---|---|
| frame=median cross=median dist=l1 | 0.566 | 0.626 | **0.495** | 0.138 | 0.80/0.63 |
| frame=mean&nbsp;&nbsp; cross=median dist=l2 | 0.527 | 0.574 | 0.476 | 0.163 | 0.76/0.64 |
| frame=median cross=median dist=l2 | 0.527 | 0.574 | 0.476 | 0.175 | 0.77/0.63 |
| frame=mean&nbsp;&nbsp; cross=median dist=l1 | 0.535 | 0.591 | 0.472 | 0.175 | 0.79/0.64 |
| frame=median cross=mean&nbsp;&nbsp; dist=l2 | 0.496 | 0.539 | 0.445 | 0.138 | 0.71/0.68 |
| **frame=mean&nbsp;&nbsp; cross=mean&nbsp;&nbsp; dist=l2** (baseline) | 0.481 | 0.522 | 0.434 | 0.150 | 0.74/0.67 |
| frame=mean&nbsp;&nbsp; cross=mean&nbsp;&nbsp; dist=l1 | 0.442 | 0.461 | 0.432 | 0.125 | 0.73/0.71 |
| frame=median cross=mean&nbsp;&nbsp; dist=l1 | 0.419 | 0.461 | 0.362 | 0.100 | 0.74/0.70 |

Baseline = `mean / mean / l2` = current calibration behaviour.

### Reading the grid

- **Cross-segment median is decisive.** The top 4 rows are exactly the 4 `cross=median`
  conditions (macro-F1 0.472–0.495); the bottom 4 are the `cross=mean` conditions
  (0.362–0.445). This factor dominates everything else.
- **Per-frame median is minor and conditional.** It helps only when paired with cross-median
  *and* L1 (0.476 → 0.495); under L2 it is a wash (0.476 = 0.476); paired with cross-mean it
  *hurts* (0.434 → 0.445 vs 0.362 etc.). Low-confidence factor.
- **Distance metric tracks the statistic.** L1 wins only alongside full median (the
  median↔L1 / mean↔L2 pairing predicted in the spec). L1 + mean is the worst combination.

## Per-tone F1 — baseline vs best

| tone | support | baseline F1 | best F1 | Δ |
|---|---|---|---|---|
| clean | 12 | 0.293 | 0.400 | +0.107 |
| edge | 34 | 0.531 | 0.582 | +0.051 |
| crunch | 46 | 0.474 | **0.635** | **+0.161** |
| metal | 23 | 0.623 | 0.731 | +0.108 |
| other | 14 | 0.250 | 0.125 | **−0.125** |

The crunch gain dominates the macro-F1 improvement and is directly explained by the
diagnostic below. `other` regresses (see Caveats).

## Confusion matrix — best condition (median / median / L1)

| true \ pred | clean | edge | crunch | metal | other |
|---|---|---|---|---|---|
| **clean** | 10 | 2 | 0 | 0 | 0 |
| **edge** | 10 | 16 | 8 | 0 | 0 |
| **crunch** | 6 | 3 | 27 | 9 | 1 |
| **metal** | 0 | 0 | 4 | 19 | 0 |
| **other** | 12 | 0 | 0 | 1 | 1 |

(ambient column/row omitted — no labels, never predicted.)

## Diagnostic: why median wins (the outlier mechanism)

Crunch active-feature spread across its 46 segments:

| feature | mean | median | note |
|---|---|---|---|
| flatness | **0.046** | **0.010** | mean is 4.6× the median |
| rms | 0.324 | 0.366 | mean dragged *down* by silent frames |

The mean is inflated by a handful of segments with flatness 0.69 / 0.39 / 0.32 / 0.24 — all
of which the near-silent report flags as ≥96% silent. A silent stem reads as a flat,
high-flatness noise floor (ISSUE-003 D). The *mean* archetype absorbs this; the *median*
discards it, producing a crunch archetype that matches real crunch.

**Data-quality flag (independent of mean/median):** these segments are labeled `crunch` but
are essentially silence — almost certainly mislabels or boundary tails that should be `other`:
- `I_hate_everything_about_you_3DG` 223–229s — 100% silent
- `carry_on_my_wayward_son_Kansas` 166–169s — 100% silent
- `carry_on_my_wayward_son_Kansas` 252–254s — 99% silent
- `let_it_die_3DG` 133–135s — 96% silent

Relabeling these to `other` (or fixing their boundaries) should lift crunch further and would
help the *mean* too — worth doing regardless of the statistic chosen.

## Hypothesis scorecard

- **H1 (per-frame median helps)** — *weakly supported.* Helps only with cross-median + L1;
  neutral or harmful otherwise.
- **H2 (cross-segment median helps)** — **strongly supported.** The dominant effect.
- **H3 (median won't fix edge/crunch overlap)** — **supported.** edge↔crunch confusion moved
  only 0.150 → 0.138, and edge bleeds into clean (10/34). Separability is feature-space
  limited; the 13 MFCCs and a distortion-sensitive feature remain the real lever (ISSUE-003 B).

## Recommendation

1. **Adopt cross-segment median now — low risk, high value.** Change `run_calibrate.py:105`
   from `np.mean(vecs, axis=0)` to `np.median(vecs, axis=0)`. This captures the bulk of the
   gain (macro-F1 0.434 → 0.476), is a **one-line change**, and needs **no runtime change**:
   the classifier only compares against whatever vectors land in `archetypes.json`, so there
   is no consistency invariant to honor here.
2. **Treat per-frame median + L1 as an optional follow-up.** It adds 0.476 → 0.495 but costs
   more surface area and risk: per-frame median must change in **both** `pipeline.py:59` and
   `run_calibrate.py:87` together (consistency invariant), and L1 means swapping the
   classifier's distance metric in `tone_classifier.py:76`. Validate separately before
   committing.
3. **Fix the mislabeled silent crunch segments** (above) via the correction CLI, then
   re-run calibration. Orthogonal to the statistic and helps either way.
4. **edge/crunch overlap is still open (ISSUE-003).** Median is not the fix. Next:
   activate/weight the MFCCs or add a distortion feature so the feature space actually
   separates the two tones.

## Caveats

- **`other` detection regressed and is generally weak.** Near-silent `other` segments
  classify as `clean` (12/14) because the confidence floor (0.2) rarely fires. This is a
  confidence-floor / silence-gating problem, *separate* from mean-vs-median, and a candidate
  for its own fix (e.g. an explicit low-RMS → `other` gate).
- **Absolute accuracy is modest (~57–63%).** This experiment ranks the *statistic*, it does
  not certify the classifier as production-accurate; the edge/crunch/clean confusions are the
  ceiling and require the feature-space work above.
- ambient is untested (0 labels) — conclusions do not extend to it.

## Reproduce

```
python -m guitar_helper.experiments.calibration_eval            # full grid -> results/exp-001/
python -m guitar_helper.experiments.calibration_eval --diagnose # outlier diagnostic only
```

CI-safe unit tests for the statistic/distance primitives: `tests/test_calibration_eval.py`.

---

## Update (2026-06-24) — median recommendation reversed after re-label

The original 129 labels were lost (re-analysis wiped `manually_corrected`; unrecoverable).
After re-analysing and re-labeling all 9 tracks from scratch — this time marking the silent
segments correctly as `other` and adding the new `overdrive` tone — the grid was re-run.

**On the clean data, mean now beats median:**

| condition | macro-F1 |
|---|---|
| `mean / mean / L2` (pre-median behaviour) | **0.693** |
| `mean / median / L2` (the EXP-001 median pick) | 0.660 |

The median advantage was entirely an artifact of the silent-`crunch` mislabels inflating the
mean (the mechanism in the diagnostic above). With those fixed, the contamination is gone and
plain mean is better. **`run_calibrate.py` was reverted to `np.mean`** (cross-segment); median
stays documented as the fallback if future labels reintroduce outliers.

Other changes vs the original run:
- **macro-F1 0.434 → ~0.69** overall (clean data + new tone).
- **edge↔crunch confusion 0.150 → 0.000** — ISSUE-003 effectively resolved; the `overdrive`
  class absorbs the mid-gain overlap. Clean↔edge is the new (smaller) ceiling.
- Per-tone F1: metal 0.89, crunch 0.82, overdrive 0.71, other 0.69, clean 0.56, edge 0.48.
- `overdrive` has only 5 labelled segments — least robust archetype; label more to refine.
