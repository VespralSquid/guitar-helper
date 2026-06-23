from __future__ import annotations

import numpy as np
import pytest

from guitar_helper.analysis.feature_extractor import N_FEATURES, FeatureExtractor

SR = 22050
HOP = 512


@pytest.fixture
def sine_wave() -> np.ndarray:
    t = np.arange(SR, dtype=np.float32) / SR
    return (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def test_shape(sine_wave):
    matrix = FeatureExtractor().extract(sine_wave, SR)
    expected_frames = 1 + len(sine_wave) // HOP
    assert matrix.shape == (N_FEATURES, expected_frames)


def test_normalised_range(sine_wave):
    matrix = FeatureExtractor().extract(sine_wave, SR)
    assert matrix.min() >= -1e-6
    assert matrix.max() <= 1 + 1e-6


def test_dtype(sine_wave):
    matrix = FeatureExtractor().extract(sine_wave, SR)
    assert matrix.dtype == np.float32


def test_constant_row_does_not_raise():
    y = np.zeros(SR, dtype=np.float32)
    matrix = FeatureExtractor().extract(y, SR)
    assert matrix.shape[0] == N_FEATURES


def test_clf_shape_and_range(sine_wave):
    matrix = FeatureExtractor().extract_for_classification(sine_wave, SR)
    expected_frames = 1 + len(sine_wave) // HOP
    assert matrix.shape == (N_FEATURES, expected_frames)
    assert matrix.min() >= -1e-6
    assert matrix.max() <= 1 + 1e-6
    assert matrix.dtype == np.float32


def test_clf_differs_from_per_song_normalized(sine_wave):
    ext = FeatureExtractor()
    seg = ext.extract(sine_wave, SR)
    clf = ext.extract_for_classification(sine_wave, SR)
    assert not np.allclose(seg, clf)
