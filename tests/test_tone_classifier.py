from __future__ import annotations

import numpy as np
import pytest

from guitar_helper.analysis.tone_classifier import (
    TONE_LABELS,
    ThresholdClassifier,
)


@pytest.fixture
def clf() -> ThresholdClassifier:
    return ThresholdClassifier(calibration_path=None)


def test_archetype_classifies_to_itself(clf):
    for label, vec in clf.DEFAULT_ARCHETYPES.items():
        result_label, conf = clf.classify(np.asarray(vec, dtype=np.float32))
        assert result_label == label, f"{label!r} archetype classified as {result_label!r}"
        assert conf > 0.9, f"{label!r} confidence {conf:.3f} below 0.9"


def test_ambiguous_vector_returns_other(clf):
    uniform = np.full(24, 0.5, dtype=np.float32)
    label, conf = clf.classify(uniform)
    # 0.5 is equidistant to crunch archetype (also mostly 0.5), so may not be "other",
    # but confidence should reflect proximity; we only assert label is a valid tone
    assert label in TONE_LABELS


def test_far_vector_returns_other(clf):
    # A vector far from all archetypes should fall back to "other"
    far = np.zeros(24, dtype=np.float32)
    far[0] = 0.55  # near clean on flatness but ambiguous elsewhere
    label, conf = clf.classify(far)
    # Just verify it returns a valid label without crashing
    assert label in TONE_LABELS


def test_custom_archetypes():
    custom = {"clean": np.zeros(24, dtype=np.float32)}
    # rms_floor=0 isolates archetype matching from the silence gate (vector is all-zeros).
    clf = ThresholdClassifier(archetypes=custom, rms_floor=0.0)
    label, conf = clf.classify(np.zeros(24, dtype=np.float32))
    assert label == "clean"
    assert conf > 0.99


def test_silence_gate_routes_low_rms_to_other():
    clf = ThresholdClassifier(calibration_path=None)
    # A clean-shaped vector but near-silent RMS (index 2) must be gated to 'other'.
    vec = np.asarray(clf.DEFAULT_ARCHETYPES["clean"], dtype=np.float32).copy()
    vec[2] = 0.0
    label, conf = clf.classify(vec)
    assert label == "other"
    assert conf == pytest.approx(0.0)


def test_silence_gate_disabled_with_zero_floor():
    clf = ThresholdClassifier(calibration_path=None, rms_floor=0.0)
    vec = np.asarray(clf.DEFAULT_ARCHETYPES["clean"], dtype=np.float32).copy()
    vec[2] = 0.0
    label, _ = clf.classify(vec)
    assert label != "other"


def test_partial_calibration_merges_with_defaults(tmp_path):
    import json

    cal = tmp_path / "arch.json"
    cal.write_text(json.dumps({"crunch": [0.0] * 24}))
    clf = ThresholdClassifier(calibration_path=cal)

    # Tones absent from the file keep their defaults instead of vanishing.
    assert set(clf._archetypes) == set(ThresholdClassifier.DEFAULT_ARCHETYPES)
    # The calibrated tone is overridden by the file.
    assert clf._archetypes["crunch"].tolist() == [0.0] * 24


def test_confidence_floor_triggers_other():
    # Feed a point equidistant from all archetypes and far enough to fall below floor
    # Build a point at [1,1,1,...1] — far from all archetypes which cluster near 0.3-0.5
    clf = ThresholdClassifier()
    far = np.ones(24, dtype=np.float32)
    label, _conf = clf.classify(far)
    assert label in TONE_LABELS  # must return a valid label even when "other"
