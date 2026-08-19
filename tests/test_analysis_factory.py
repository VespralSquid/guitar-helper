from __future__ import annotations

import json

import numpy as np

from guitar_helper.analysis.factory import build_pipeline
from guitar_helper.analysis.pipeline import AnalysisPipeline
from guitar_helper.analysis.source_separator import AudioSeparator, NullSeparator
from guitar_helper.analysis.tone_classifier import ThresholdClassifier
from guitar_helper.config import AppConfig


class _StubStore:
    pass


def _config(tmp_path, **overrides) -> AppConfig:
    return AppConfig.resolve(tmp_path, **overrides)


def test_build_pipeline_returns_a_pipeline(tmp_path):
    pipeline = build_pipeline(_config(tmp_path), _StubStore())

    assert isinstance(pipeline, AnalysisPipeline)


def test_build_pipeline_never_uses_the_test_only_null_separator(tmp_path):
    pipeline = build_pipeline(_config(tmp_path), _StubStore())

    assert isinstance(pipeline._separator, AudioSeparator)
    assert not isinstance(pipeline._separator, NullSeparator)


def test_separator_caches_under_the_configured_stems_dir(tmp_path):
    config = _config(tmp_path)

    pipeline = build_pipeline(config, _StubStore())

    assert pipeline._separator._cache_dir == config.stems_dir


def test_model_dir_is_passed_through_as_a_string(tmp_path):
    config = _config(tmp_path, model_dir=tmp_path / "weights")

    pipeline = build_pipeline(config, _StubStore())

    assert pipeline._separator._model_dir == str(config.model_dir)


def test_model_dir_stays_none_when_unset(tmp_path):
    config = _config(tmp_path)
    assert config.model_dir is None

    pipeline = build_pipeline(config, _StubStore())

    assert pipeline._separator._model_dir is None


def test_classifier_loads_the_configured_archetypes(tmp_path):
    archetypes = tmp_path / "archetypes.json"
    vector = [0.125] * 24
    archetypes.write_text(json.dumps({"clean": vector}))
    config = _config(tmp_path, archetypes=archetypes)

    pipeline = build_pipeline(config, _StubStore())

    assert isinstance(pipeline._classifier, ThresholdClassifier)
    assert np.allclose(pipeline._classifier._archetypes["clean"], np.asarray(vector))


def test_verbose_reaches_the_separator(tmp_path):
    pipeline = build_pipeline(_config(tmp_path), _StubStore(), verbose=True)

    assert pipeline._separator._verbose is True


def test_building_a_pipeline_does_no_separation_work(tmp_path):
    config = _config(tmp_path)

    build_pipeline(config, _StubStore())

    assert not config.stems_dir.exists() or not any(config.stems_dir.iterdir())
