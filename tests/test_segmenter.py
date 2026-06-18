from __future__ import annotations

import numpy as np
import pytest

from guitar_helper.analysis.feature_extractor import FeatureExtractor
from guitar_helper.analysis.segmenter import Segmenter

SR = 22050


@pytest.fixture
def feature_matrix(make_wav):
    path = make_wav(duration_s=4.0)
    y = np.memmap(str(path), dtype=np.int16, mode="r")
    # Load via soundfile for a proper float array
    import soundfile as sf
    y, _ = sf.read(str(path), dtype="float32")
    return FeatureExtractor().extract(y, SR)


def test_boundaries_always_include_endpoints(feature_matrix):
    seg = Segmenter()
    duration_ms = int(feature_matrix.shape[1] * 512 / SR * 1000)
    bounds = seg.find_boundaries(feature_matrix, SR, duration_ms=duration_ms)
    assert bounds[0] == 0
    assert bounds[-1] == duration_ms


def test_boundaries_sorted_and_unique(feature_matrix):
    bounds = Segmenter().find_boundaries(feature_matrix, SR)
    assert bounds == sorted(set(bounds))


def test_explicit_k1(feature_matrix):
    bounds = Segmenter().find_boundaries(feature_matrix, SR, k=1)
    assert len(bounds) == 2
    assert bounds[0] == 0


def test_explicit_k2(feature_matrix):
    bounds = Segmenter().find_boundaries(feature_matrix, SR, k=2)
    assert len(bounds) >= 2
    assert bounds[0] == 0


def test_tiny_matrix_returns_two_boundaries():
    tiny = np.random.rand(24, 1).astype(np.float32)
    bounds = Segmenter().find_boundaries(tiny, SR, duration_ms=100)
    assert bounds == [0, 100]
