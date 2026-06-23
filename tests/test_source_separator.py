from __future__ import annotations

import sys
from pathlib import Path

from guitar_helper.analysis.source_separator import AudioSeparator, NullSeparator


def test_null_separator_returns_input_unchanged(tmp_path):
    src = tmp_path / "song.wav"
    src.write_bytes(b"")
    result = NullSeparator().separate_guitar(src, "abc123")
    assert result == src


def test_audio_separator_cache_hit_skips_separation(tmp_path, monkeypatch):
    # Pre-create the cached stem; separation must not be invoked.
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "deadbeef"
    cached = cache / f"{file_hash}_guitar.wav"
    cached.write_bytes(b"fake-wav")

    def _boom(*_a, **_k):
        raise AssertionError("Separator must not be imported/called on a cache hit")

    monkeypatch.setitem(
        sys.modules, "audio_separator", type(sys)("audio_separator")
    )
    monkeypatch.setitem(
        sys.modules, "audio_separator.separator", type(sys)("audio_separator.separator")
    )
    sys.modules["audio_separator.separator"].Separator = _boom

    sep = AudioSeparator(cache_dir=cache)
    result = sep.separate_guitar(tmp_path / "song.wav", file_hash)
    assert result == cached


def test_audio_separator_cache_key_naming(tmp_path):
    sep = AudioSeparator(cache_dir=tmp_path / "stems")
    # On a cache miss the real lib would run; we only assert the derived cache path
    # by pre-seeding it and confirming the same path is returned.
    cache = tmp_path / "stems"
    cache.mkdir()
    expected = cache / "hash99_guitar.wav"
    expected.write_bytes(b"x")
    assert sep.separate_guitar(tmp_path / "in.wav", "hash99") == expected


def test_source_separator_imports_without_audio_separator():
    # Importing the module must not require the heavy optional dependency.
    import importlib

    mod = importlib.import_module("guitar_helper.analysis.source_separator")
    assert hasattr(mod, "AudioSeparator")
    assert "audio_separator" not in repr(mod)


def test_audio_separator_default_cache_dir():
    assert AudioSeparator()._cache_dir == Path("stems")
