# Segmenter & Classifier Bug Fix Report

## Overview

Two critical bugs in the analysis pipeline prevented proper audio segmentation and tone classification. Both have been fixed and tested.

- **Segmenter**: Structural boundary detection (k-estimation) was unreliable due to an inverted relationship between song uniformity and threshold.
- **Classifier Confidence**: Confidence scoring was inflated, causing archetype discrimination to fail.

---

## Bug 1 — Segmenter Boundary Detection (`segmenter.py`)

### Symptom

Two opposing failure modes appeared during testing:

- **Undersegmentation**: Only 1 segment returned (no structural boundaries found)
- **Oversegmentation**: Verbose output showed the hardcoded cap of 12 segments hit repeatedly

### Root Cause

The `_estimate_k()` method in `guitar_helper/analysis/segmenter.py` used:

```python
find_peaks(smoothed, prominence=smoothed.std(), distance=window)
```

The `prominence` parameter measures how much a peak stands above its **local surroundings**. Setting it equal to `smoothed.std()` created an inverted relationship with actual song structure:

- **Sharp, clear transitions** (high std) → high prominence threshold → structural boundaries missed
- **Gradual, uniform content** (low std) → low threshold → every small noise bump qualifies as a peak

Additionally, `_MAX_AUTO_K = 12` was too permissive; typical songs have 5–8 structural sections.

### Fix Applied

- Switched from `prominence` to `height` threshold:
  ```python
  find_peaks(smoothed, height=smoothed.mean() + smoothed.std(), distance=window)
  ```
  
- `height` is **absolute** — a peak must exceed a fixed threshold value, not be relative to neighbours. This correctly identifies peaks genuinely elevated above the average distance, which is what structural transitions produce.

- Lowered `_MAX_AUTO_K` from 12 to 8 to match realistic song structure.

- Verbose output now prints `mean`, `std`, and `height_threshold` for diagnostics.

---

## Bug 2 — Classifier Confidence Inflation (`tone_classifier.py`)

### Symptom

Classification defaulted to "clean" or "ambient" for all segments regardless of audio content. The "other" fallback label was never returned.

### Root Cause

The `classify()` method in `guitar_helper/analysis/tone_classifier.py` computed:

```python
conf = 1.0 - best_dist / sqrt(24)
```

Three issues compound here:

1. **Incorrect normalisation**: `sqrt(24) ≈ 4.9` is the theoretical maximum Euclidean distance in a 24-dimensional unit hypercube. Practical inter-archetype distances are far smaller (0.1–1.5), so every classification scored ~0.8+ confidence, rendering the `_CONFIDENCE_FLOOR = 0.2` threshold ineffective.

2. **Uninformative archetype values**: All 13 MFCC dimensions and 7 spectral contrast bands were hardcoded to 0.5 in every archetype. Equal offsets across all archetypes provide zero discriminating power.

### Fix Applied

- **Relative confidence scaling**: `conf = 1.0 - best_dist / worst_dist`
  - 1.0 = unambiguous winner (all other archetypes far away)
  - 0.0 = all archetypes equidistant (pure ambiguity)
  - This avoids reliance on theoretical bounds and adapts to actual archetype separation.

- **Physically motivated spectral contrast (indices 4–10)**:
  - clean = 0.75 (high harmonic clarity)
  - crunch = 0.50 (moderate contrast)
  - metal = 0.20 (low contrast, high noise floor)
  - ambient = 0.80 (very high clarity, clean sustain)

- Removed `_MAX_DIST` constant and unnecessary `math` import.

- The classifier now uses **11 of 24 features** (4 core MFCC + 7 spectral contrast) with meaningful confidence discrimination.

---

## Classification Status & Next Steps

### Current State

The classifier produces reliable confidence scores and correctly labels archetype-matched segments in test data. However, all archetype feature values are physically motivated **estimates**, not calibrated against real recordings.

Within-song min-max normalisation in `feature_extractor.py` means archetype values represent **relative position** within a song's dynamic range, not absolute feature levels. This normalisation is correct, but it means archetypes must match the statistical distribution of real data within each song.

### Calibration Required

Robust, production-ready classification requires:

1. Record or source 5–10 known-tone guitar samples (clean, crunch, metal, ambient), representing diverse amp/instrument/recording setups
2. Run each sample through the full analysis pipeline end-to-end
3. Manually verify segment labels and note the mean normalised feature vector for each labelled segment
4. Update `DEFAULT_ARCHETYPES` in `tone_classifier.py` with measured values
5. Re-test across a holdout set of different songs

Without calibration, classification accuracy depends on chance similarity between estimated and real feature distributions. With calibration, the relative distance/confidence approach will be robust to within-song normalisation.
