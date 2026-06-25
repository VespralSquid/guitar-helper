from __future__ import annotations

import numpy as np
import pytest

from guitar_helper.experiments.calibration_eval import (
    aggregate,
    build_archetypes,
    classify,
    distance,
    reduce_frames,
)


def test_reduce_frames_mean_vs_median():
    # Column 4 is an outlier frame; median ignores it, mean does not.
    mat = np.array([[1.0, 1.0, 1.0, 1.0, 100.0]])
    assert reduce_frames(mat, "mean")[0] == pytest.approx(20.8)
    assert reduce_frames(mat, "median")[0] == pytest.approx(1.0)


def test_aggregate_mean_vs_median():
    vecs = [np.array([0.0, 0.0]), np.array([2.0, 2.0]), np.array([100.0, 100.0])]
    assert aggregate(vecs, "mean").tolist() == pytest.approx([34.0, 34.0])
    assert aggregate(vecs, "median").tolist() == pytest.approx([2.0, 2.0])


def test_distance_l2_vs_l1():
    a, b = np.array([0.0, 0.0]), np.array([3.0, 4.0])
    assert distance(a, b, "l2") == pytest.approx(5.0)
    assert distance(a, b, "l1") == pytest.approx(7.0)


@pytest.mark.parametrize("metric", ["l2", "l1"])
def test_classify_picks_nearest_archetype(metric):
    arch = {"clean": np.zeros(3), "metal": np.ones(3) * 10}
    label, _ = classify(np.array([0.1, 0.1, 0.1]), arch, metric)
    assert label == "clean"
    label, _ = classify(np.array([9.0, 9.0, 9.0]), arch, metric)
    assert label == "metal"


def test_classify_confidence_floor_returns_other():
    # Equidistant from both archetypes -> confidence 0 -> below floor -> "other".
    arch = {"clean": np.array([0.0]), "metal": np.array([10.0])}
    label, conf = classify(np.array([5.0]), arch, "l2")
    assert label == "other"
    assert conf == pytest.approx(0.0)


def test_build_archetypes_excludes_holdout_and_other():
    seg_vecs = [
        ("clean", np.array([0.0] * 24)),
        ("clean", np.array([1.0] * 24)),
        ("clean", np.array([2.0] * 24)),
        ("other", np.array([99.0] * 24)),
    ]
    # Hold out index 0; remaining clean members are [1] and [2] -> mean 1.5.
    arch = build_archetypes(seg_vecs, exclude_idx=0, cross_stat="mean")
    assert arch["clean"][0] == pytest.approx(1.5)
    # "other" never becomes an archetype.
    assert "other" not in arch


def test_build_archetypes_falls_back_to_default_when_too_few_members():
    from guitar_helper.analysis.tone_classifier import ThresholdClassifier

    seg_vecs = [("clean", np.array([5.0] * 24))]  # only one clean -> below _MIN_MEMBERS
    arch = build_archetypes(seg_vecs, exclude_idx=-1, cross_stat="median")
    expected = np.asarray(ThresholdClassifier.DEFAULT_ARCHETYPES["clean"])
    assert np.allclose(arch["clean"], expected)
