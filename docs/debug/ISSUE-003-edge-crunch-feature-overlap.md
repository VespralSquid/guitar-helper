# ISSUE-003 — edge vs crunch not separable by archetype tuning

**Status:** Open (diagnosed, no fix applied)  
**Date:** 2026-06-19

---

## Symptom
Manually tuning the `crunch` archetype to fix edge/crunch mislabeling causes segment labels to oscillate (crunch→edge→crunch across runs) instead of converging. Moving the archetype shuffles which mid-song segments read crunch but never stably matches what the ear hears.

All classification confidences stay in a narrow low band (~0.22–0.31) regardless of tuning. On the test song "I Hate Everything About You" (3DG), the guitar section heard as crunch (~0:54–1:26) cannot be made to read crunch without also flipping genuine edge sections to crunch.

## Root cause (three compounding factors)
**1. Edge and crunch are nearly coincident in the current feature space.** Mean feature vectors measured directly from the separated guitar stem:

| Region | flat | zcr | rms | cen | contrast |
|---|---|---|---|---|---|
| edge-truth 0:10–0:50 | 0.016 | 0.465 | 0.237 | 0.167 | 0.575 |
| crunch-truth 0:57–1:24 | 0.021 | 0.504 | 0.284 | 0.166 | 0.523 |

They differ by only ~0.04 (zcr/rms) and ~0.05 (contrast) — essentially one cloud, not two clusters. Any decision boundary is arbitrary and overfits a single song; this is why binary-searching the boundary does not converge.

**2. Archetypes rare on the wrong absolute scale.** Edge/cunch archetypes assume flatness 0.20–0.40, but real separated-stem flatness is ~0.02 — off by 10–20×. Because the real data is far from every archetype, none wins clearly, explaining the stuck low confidences.

**3. The 13 MFCCs are unused.** Edge-vs-crunch is mainly timbre/harmonic-distortion, which lives in the MFCCs — but `_archetype()` pins all 13 MFCC dimensions to 0.5, contributing zero discrimination. The classifier is blind to the feature family that separates these tones.

## What was tried
- Raised crunch archetype up the gain axis (0.35/0.40/0.40/0.25/c0.50 → 0.45/0.48/0.45/0.30/c0.42): edge captured more, some crunch→edge.
- Then midpoint (0.40/0.44/0.425/0.275/c0.46): some segments flipped back to crunch. Net effect = oscillation, not calibration — confirming the overlap.

## Proposed fixes (priority order)
**A. (Recommended) Data-driven calibration:** label a handful of segments per tone as ground truth via the existing correction CLI (`run_correction` sets `manually_corrected=1`), then set each archetype = mean 24-dim `extract_for_classification` vector of its labeled segments. Automatically fixes the scale AND activates the MFCCs.

**B. Stop pinning the 13 MFCCs to 0.5** — include them (and/or add a distortion-sensitive feature) so the feature set captures edge-vs-crunch timbre.

**C. Calibrate across multiple songs** (separate the other two tracks first) to avoid single-song overfit.

**D. Handle low-energy/near-silent regions separately:** the outro 3:43–3:49 measured rms≈0.000 with flat=0.728/zcr=0.996 (noise-floor artifact) and misclassifies as crunch/metal.

## Current status
Diagnosed; no fix applied. The crunch archetype currently sits at midpoint values (0.40/0.44/0.425/0.275/c0.46). Decision pending: data-driven calibration (A+B+C, recommended) vs continued manual archetype tuning (expected to keep overfitting).
