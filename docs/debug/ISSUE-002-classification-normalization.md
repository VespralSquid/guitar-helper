# ISSUE-002 — Classification always returns clean/ambient

**Status:** Resolved  
**Date:** 2026-06-18

---

## Symptom
All segments classified as `clean` or `crunch` regardless of actual guitar tone. Confidence values clustered around 0.3. `metal` and `ambient` archetypes never matched.

## Root cause
`FeatureExtractor.extract()` applies per-song min-max normalization (maps each feature row's [min, max] within the song to [0, 1]). This destroys absolute spectral character: a clean guitar and a metal guitar both have their full dynamic range mapped to [0, 1] within the song. The mean of any segment ends up near 0.3–0.5 regardless of tone. `ThresholdClassifier` archetypes were calibrated to absolute physical values (metal at flatness=0.9), which are unreachable in per-song normalized space.

## Fix
Added `FeatureExtractor.extract_for_classification(y, sr)` that uses fixed-range clipping instead of per-song normalization:
- flatness, zcr, rms: clipped to `[0, 0.25]` (typical music range)
- centroid: clipped to `[0, sr/2]` (Nyquist frequency)
- spectral contrast: clipped to `[0, 40 dB]`
- MFCC[0]: clipped to `[-300, 50]`
- MFCC[1:]: clipped to `[-60, 60]`

`AnalysisPipeline.run()` now extracts two matrices from the same raw features:
- `feature_matrix` (per-song normalized) → passed to `Segmenter` (unchanged)
- `clf_matrix` (fixed-range normalized) → used for segment mean vectors passed to `ThresholdClassifier`

Updated `ThresholdClassifier.DEFAULT_ARCHETYPES` to values calibrated to fixed-range scale (e.g., metal: flatness=0.60, zcr=0.60, contrast=0.25 vs clean: flatness=0.12, zcr=0.22, contrast=0.75).

## Result after fix (sample songs)
| Song | Segments | Tones seen |
|---|---|---|
| Gunslinger (A7X) | 8 | edge, crunch |
| I Hate Everything About You (3DG) | 8 | edge, crunch |
| Carry On My Wayward Son (Kansas) | 8 | metal, crunch, edge |

Confidence values: 0.24–0.47 (expected for uncalibrated archetypes).

## Remaining work
Calibration pass needed: run pipeline on 5–10 songs with known tone sections, measure actual fixed-range normalized feature means per tone, update `DEFAULT_ARCHETYPES` to match observed values.
