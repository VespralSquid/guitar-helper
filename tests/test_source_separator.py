from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from guitar_helper.analysis.source_separator import (
    AudioSeparator,
    NullSeparator,
    SeparationError,
    _sample_digest,
)


def _write_wav(path: Path, seconds: float = 1.0, samplerate: int = 44100, channels: int = 2, silent: bool = False) -> Path:
    frames = int(seconds * samplerate)
    if silent:
        data = np.zeros((frames, channels), dtype="float32")
    else:
        rng = np.random.default_rng(1234)
        data = (rng.standard_normal((frames, channels)) * 0.1).astype("float32")
    sf.write(str(path), data, samplerate, subtype="PCM_16")
    return path


def _zero_fill_in_place(path: Path) -> None:
    size = path.stat().st_size
    with path.open("r+b") as f:
        f.seek(44)
        f.write(b"\x00" * (size - 44))
    assert path.stat().st_size == size


def _install_boom_separator(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("Separator must not be imported/called on a cache hit")

    monkeypatch.setitem(sys.modules, "audio_separator", type(sys)("audio_separator"))
    monkeypatch.setitem(sys.modules, "audio_separator.separator", type(sys)("audio_separator.separator"))
    monkeypatch.setattr(sys.modules["audio_separator.separator"], "Separator", _boom, raising=False)


class _StubSeparator:
    """Records invocation; writes a wav of `out_seconds` into output_dir on separate()."""

    calls: list[dict] = []

    def __init__(self, output_dir, output_format, output_single_stem, model_file_dir=None):
        self.output_dir = Path(output_dir)
        self.output_single_stem = output_single_stem

    def load_model(self, model_filename):
        self.model_filename = model_filename

    def separate(self, input_path, output_names):
        _StubSeparator.calls.append({"input_path": input_path, "output_names": output_names})
        stem_name = list(output_names.values())[0]
        out = self.output_dir / f"{stem_name}.wav"
        _write_wav(out, seconds=_StubSeparator.out_seconds)
        return [str(out)]


def _install_stub_separator(monkeypatch, out_seconds: float):
    _StubSeparator.calls = []
    _StubSeparator.out_seconds = out_seconds
    monkeypatch.setitem(sys.modules, "audio_separator", type(sys)("audio_separator"))
    monkeypatch.setitem(sys.modules, "audio_separator.separator", type(sys)("audio_separator.separator"))
    monkeypatch.setattr(sys.modules["audio_separator.separator"], "Separator", _StubSeparator, raising=False)
    return _StubSeparator


def _no_sleep(monkeypatch):
    import guitar_helper.analysis.source_separator as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)


# ---------------------------------------------------------------------------
# Pre-existing behaviour, unaffected
# ---------------------------------------------------------------------------


def test_null_separator_returns_input_unchanged(tmp_path):
    src = tmp_path / "song.wav"
    src.write_bytes(b"")
    result = NullSeparator().separate_guitar(src, "abc123")
    assert result == src


def test_audio_separator_default_cache_dir():
    assert AudioSeparator()._cache_dir == Path("stems")


def test_source_separator_imports_without_audio_separator():
    import importlib

    mod = importlib.import_module("guitar_helper.analysis.source_separator")
    assert hasattr(mod, "AudioSeparator")
    assert "audio_separator" not in repr(mod)
    assert "torch" not in sys.modules
    assert "onnxruntime" not in sys.modules


# ---------------------------------------------------------------------------
# Test 12 (rewritten) — a valid cache hit never imports the separator
# ---------------------------------------------------------------------------


def test_audio_separator_cache_hit_skips_separation(tmp_path, monkeypatch):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "deadbeef"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=1.0)

    sep = AudioSeparator(cache_dir=cache)
    manifest = sep._build_manifest(cached, file_hash, sep._source_duration_ms(cached), grandfathered=False)
    (cache / f"{file_hash}_guitar.json").write_text(json.dumps(manifest))

    _install_boom_separator(monkeypatch)

    result = sep.separate_guitar(tmp_path / "song.wav", file_hash)
    assert result == cached


def test_audio_separator_cache_key_naming(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    sep = AudioSeparator(cache_dir=cache)
    expected = cache / "hash99_guitar.wav"
    _write_wav(expected, seconds=1.0)
    manifest = sep._build_manifest(expected, "hash99", sep._source_duration_ms(expected), grandfathered=False)
    (cache / "hash99_guitar.json").write_text(json.dumps(manifest))

    assert sep.separate_guitar(tmp_path / "in.wav", "hash99") == expected


# ---------------------------------------------------------------------------
# 1. Truncated stem is a miss
# ---------------------------------------------------------------------------


def test_truncated_stem_is_stem_bytes_miss(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash1"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=2.0)
    sep = AudioSeparator(cache_dir=cache)
    manifest = sep._build_manifest(cached, file_hash, sep._source_duration_ms(cached), grandfathered=False)
    manifest_path = cache / f"{file_hash}_guitar.json"
    manifest_path.write_text(json.dumps(manifest))

    original_size = cached.stat().st_size
    with cached.open("r+b") as f:
        f.truncate(original_size // 3)

    assert sep._check_cache(cached, tmp_path / "source.wav", file_hash) == "stem-bytes"


def test_truncated_stem_regenerated_manifest_is_duration_miss(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash1b"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=5.0)
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=5.0)
    sep = AudioSeparator(cache_dir=cache)
    source_ms = sep._source_duration_ms(source)
    manifest = sep._build_manifest(cached, file_hash, source_ms, grandfathered=False)
    manifest_path = cache / f"{file_hash}_guitar.json"
    manifest_path.write_text(json.dumps(manifest))

    with cached.open("r+b") as f:
        f.truncate(cached.stat().st_size // 3)

    truncated_manifest = sep._build_manifest(cached, file_hash, source_ms, grandfathered=False)
    manifest_path.write_text(json.dumps(truncated_manifest))

    assert sep._check_cache(cached, source, file_hash) == "duration"


def test_truncated_stem_triggers_reseparation(tmp_path, monkeypatch):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash1c"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=1.0)
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=0.1)  # far shorter than source -> a genuine miss
    stub = _install_stub_separator(monkeypatch, out_seconds=1.0)

    sep = AudioSeparator(cache_dir=cache)
    result = sep.separate_guitar(source, file_hash)

    assert stub.calls, "expected the stub separator to be invoked on a miss"
    assert result == cached


# ---------------------------------------------------------------------------
# 2. Zero-filled same-size stem is a miss (F1)
# ---------------------------------------------------------------------------


def test_zero_filled_same_size_stem_is_content_miss(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash2"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=2.0)
    sep = AudioSeparator(cache_dir=cache)
    manifest = sep._build_manifest(cached, file_hash, sep._source_duration_ms(cached), grandfathered=False)
    (cache / f"{file_hash}_guitar.json").write_text(json.dumps(manifest))

    size_before = cached.stat().st_size
    frames_before = sf.info(cached).frames

    _zero_fill_in_place(cached)

    assert cached.stat().st_size == size_before
    assert sf.info(cached).frames == frames_before
    assert sep._check_cache(cached, tmp_path / "source.wav", file_hash) == "content"


# ---------------------------------------------------------------------------
# 3. Missing manifest, wav not vouched for -> miss (grandfather branch)
# ---------------------------------------------------------------------------


def test_missing_manifest_duration_mismatch_is_miss(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash3"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=2.0)
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=9.0)  # very different duration

    sep = AudioSeparator(cache_dir=cache)
    assert sep._check_cache(cached, source, file_hash) == "duration"
    assert not (cache / f"{file_hash}_guitar.json").exists()


def test_missing_manifest_no_readable_source_is_miss(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash3b"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=2.0)
    source = tmp_path / "does_not_exist.wav"

    sep = AudioSeparator(cache_dir=cache)
    assert sep._check_cache(cached, source, file_hash) == "no-source-duration"
    assert not (cache / f"{file_hash}_guitar.json").exists()


# ---------------------------------------------------------------------------
# 4. Different model identity -> miss, plus fail-soft pair
# ---------------------------------------------------------------------------


def _make_model_dir(model_dir: Path, weight_name: str = "5c90dfd2-34c22ccb.th") -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    sig = weight_name.split("-")[0]
    (model_dir / "htdemucs_6s.yaml").write_text(f"models: ['{sig}']")
    (model_dir / weight_name).write_bytes(b"x" * 128)


def test_model_mismatch_is_miss(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    model_dir = tmp_path / "models"
    _make_model_dir(model_dir)

    file_hash = "hash4"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=1.0)
    manifest = {
        "cache_format_version": 1,
        "source_hash": file_hash,
        "source_duration_ms": None,
        "stem_bytes": cached.stat().st_size,
        "stem_frames": sf.info(cached).frames,
        "stem_samplerate": sf.info(cached).samplerate,
        "stem_sample_digest": _sample_digest(cached, cached.stat().st_size)[0],
        "model": "htdemucs_6s.yaml",
        "model_files": [{"name": "deadbeef-00000000.th", "bytes": 1}],
        "separator_version": "0.0.0",
        "created_at": "2026-01-01T00:00:00+00:00",
        "grandfathered": False,
    }
    (cache / f"{file_hash}_guitar.json").write_text(json.dumps(manifest))

    sep = AudioSeparator(cache_dir=cache, model_dir=model_dir)
    assert sep._check_cache(cached, tmp_path / "source.wav", file_hash) == "model"


def test_model_mismatch_fail_soft_when_model_dir_absent(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    absent_model_dir = tmp_path / "no-such-model-dir"

    file_hash = "hash4b"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=1.0)
    manifest = {
        "cache_format_version": 1,
        "source_hash": file_hash,
        "source_duration_ms": None,
        "stem_bytes": cached.stat().st_size,
        "stem_frames": sf.info(cached).frames,
        "stem_samplerate": sf.info(cached).samplerate,
        "stem_sample_digest": _sample_digest(cached, cached.stat().st_size)[0],
        "model": "htdemucs_6s.yaml",
        "model_files": [{"name": "deadbeef-00000000.th", "bytes": 1}],
        "separator_version": "0.0.0",
        "created_at": "2026-01-01T00:00:00+00:00",
        "grandfathered": False,
    }
    (cache / f"{file_hash}_guitar.json").write_text(json.dumps(manifest))

    sep = AudioSeparator(cache_dir=cache, model_dir=absent_model_dir)
    assert sep._check_cache(cached, tmp_path / "source.wav", file_hash) is None


def test_locked_cached_wav_is_cache_miss_not_raw_oserror(tmp_path, monkeypatch):
    """A cached wav that raises OSError on read (locked by another process,
    e.g. antivirus/OneDrive) must be treated as a cache miss, not propagate a
    raw OSError past separate_guitar's SeparationError contract."""
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash-locked"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=1.0)
    manifest = {
        "cache_format_version": 1,
        "source_hash": file_hash,
        "source_duration_ms": None,
        "stem_bytes": cached.stat().st_size,
        "stem_frames": sf.info(cached).frames,
        "stem_samplerate": sf.info(cached).samplerate,
        "stem_sample_digest": _sample_digest(cached, cached.stat().st_size)[0],
        "model": "htdemucs_6s.yaml",
        "model_files": None,
        "separator_version": "0.0.0",
        "created_at": "2026-01-01T00:00:00+00:00",
        "grandfathered": False,
    }
    (cache / f"{file_hash}_guitar.json").write_text(json.dumps(manifest))

    import guitar_helper.analysis.source_separator as source_separator_module

    def _raise(path, size):
        raise OSError(13, "Permission denied (simulated lock)")

    monkeypatch.setattr(source_separator_module, "_sample_digest", _raise)

    sep = AudioSeparator(cache_dir=cache)
    assert sep._check_cache(cached, tmp_path / "source.wav", file_hash) == "stem-unreadable"


def test_model_mismatch_fail_soft_when_manifest_model_files_null(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    model_dir = tmp_path / "models"
    _make_model_dir(model_dir)

    file_hash = "hash4c"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=1.0)
    manifest = {
        "cache_format_version": 1,
        "source_hash": file_hash,
        "source_duration_ms": None,
        "stem_bytes": cached.stat().st_size,
        "stem_frames": sf.info(cached).frames,
        "stem_samplerate": sf.info(cached).samplerate,
        "stem_sample_digest": _sample_digest(cached, cached.stat().st_size)[0],
        "model": "htdemucs_6s.yaml",
        "model_files": None,
        "separator_version": "0.0.0",
        "created_at": "2026-01-01T00:00:00+00:00",
        "grandfathered": False,
    }
    (cache / f"{file_hash}_guitar.json").write_text(json.dumps(manifest))

    sep = AudioSeparator(cache_dir=cache, model_dir=model_dir)
    assert sep._check_cache(cached, tmp_path / "source.wav", file_hash) is None


# ---------------------------------------------------------------------------
# 5. cache_format_version mismatch -> miss, reported before sf.info is touched
# ---------------------------------------------------------------------------


def test_format_version_mismatch_reported_before_sf_info(tmp_path, monkeypatch):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash5"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=1.0)
    manifest = {
        "cache_format_version": 0,
        "source_hash": file_hash,
        "source_duration_ms": None,
        "stem_bytes": cached.stat().st_size,
        "stem_frames": 1,
        "stem_samplerate": 1,
        "stem_sample_digest": "irrelevant",
        "model": "htdemucs_6s.yaml",
        "model_files": None,
        "separator_version": "0.0.0",
        "created_at": "2026-01-01T00:00:00+00:00",
        "grandfathered": False,
    }
    (cache / f"{file_hash}_guitar.json").write_text(json.dumps(manifest))

    def _raise(*_a, **_k):
        raise AssertionError("sf.info must not be called before format-version is checked")

    monkeypatch.setattr(sf, "info", _raise)

    sep = AudioSeparator(cache_dir=cache)
    assert sep._check_cache(cached, tmp_path / "source.wav", file_hash) == "format-version"


# ---------------------------------------------------------------------------
# 6. Kill mid-separation leaves nothing at the cache path
# ---------------------------------------------------------------------------


def test_separator_exception_leaves_no_cache_artifacts(tmp_path, monkeypatch):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash6"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=1.0)

    class _RaisingSeparator:
        def __init__(self, **_k):
            pass

        def load_model(self, model_filename):
            pass

        def separate(self, *_a, **_k):
            raise RuntimeError("killed mid-separation")

    monkeypatch.setitem(sys.modules, "audio_separator", type(sys)("audio_separator"))
    monkeypatch.setitem(sys.modules, "audio_separator.separator", type(sys)("audio_separator.separator"))
    monkeypatch.setattr(sys.modules["audio_separator.separator"], "Separator", _RaisingSeparator, raising=False)

    sep = AudioSeparator(cache_dir=cache)
    with pytest.raises(RuntimeError):
        sep.separate_guitar(source, file_hash)

    assert not (cache / f"{file_hash}_guitar.wav").exists()
    assert not (cache / f"{file_hash}_guitar.json").exists()
    assert list(cache.glob(".tmp-*")) == []


def test_separator_returns_no_output_raises_separation_error(tmp_path, monkeypatch):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash6b"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=1.0)

    class _EmptySeparator:
        def __init__(self, **_k):
            pass

        def load_model(self, model_filename):
            pass

        def separate(self, *_a, **_k):
            return []

    monkeypatch.setitem(sys.modules, "audio_separator", type(sys)("audio_separator"))
    monkeypatch.setitem(sys.modules, "audio_separator.separator", type(sys)("audio_separator.separator"))
    monkeypatch.setattr(sys.modules["audio_separator.separator"], "Separator", _EmptySeparator, raising=False)

    sep = AudioSeparator(cache_dir=cache)
    with pytest.raises(SeparationError):
        sep.separate_guitar(source, file_hash)

    assert not (cache / f"{file_hash}_guitar.wav").exists()
    assert not (cache / f"{file_hash}_guitar.json").exists()
    assert list(cache.glob(".tmp-*")) == []


# ---------------------------------------------------------------------------
# 7. Stale .tmp-* dirs are swept
# ---------------------------------------------------------------------------


def test_stale_tmp_dirs_swept_fresh_ones_kept(tmp_path, monkeypatch):
    cache = tmp_path / "stems"
    cache.mkdir()
    old = cache / ".tmp-old"
    old.mkdir()
    (old / "leftover.txt").write_text("x")
    new = cache / ".tmp-new"
    new.mkdir()

    import os

    seven_hours_ago = time.time() - 7 * 3600
    os.utime(old, (seven_hours_ago, seven_hours_ago))

    file_hash = "hash7"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=1.0)
    _install_stub_separator(monkeypatch, out_seconds=1.0)

    sep = AudioSeparator(cache_dir=cache)
    sep.separate_guitar(source, file_hash)

    assert not old.exists()
    assert new.exists()


# ---------------------------------------------------------------------------
# 8. Grandfathering
# ---------------------------------------------------------------------------


def test_grandfathering_accepts_and_writes_manifest(tmp_path, monkeypatch):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash8"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=3.0)
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=3.0)

    _install_boom_separator(monkeypatch)

    sep = AudioSeparator(cache_dir=cache)
    result = sep.separate_guitar(source, file_hash)

    assert result == cached
    manifest_path = cache / f"{file_hash}_guitar.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["grandfathered"] is True
    assert manifest["source_duration_ms"] == sep._source_duration_ms(source)
    assert manifest["stem_sample_digest"] == _sample_digest(cached, cached.stat().st_size)[0]

    # Second call is now an ordinary manifest hit.
    assert sep._check_cache(cached, source, file_hash) is None


def test_grandfathering_refuses_all_zero_stem(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash8b"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=3.0)
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=3.0, silent=True)

    sep = AudioSeparator(cache_dir=cache)
    assert sep._check_cache(cached, source, file_hash) == "all-zero"
    assert not (cache / f"{file_hash}_guitar.json").exists()


# ---------------------------------------------------------------------------
# 9. Concurrent separations both complete, cache ends valid
# ---------------------------------------------------------------------------


def test_concurrent_separations_leave_cache_valid(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash9"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=1.0)
    _install_stub_separator(monkeypatch, out_seconds=1.0)

    sep_a = AudioSeparator(cache_dir=cache)
    sep_b = AudioSeparator(cache_dir=cache)
    cached = cache / f"{file_hash}_guitar.wav"
    manifest_path = cache / f"{file_hash}_guitar.json"

    result_a = sep_a._perform_separation(source, file_hash, cached, manifest_path)
    result_b = sep_b._perform_separation(source, file_hash, cached, manifest_path)

    assert result_a == cached
    assert result_b == cached
    assert cached.exists()
    assert sep_a._check_cache(cached, source, file_hash) is None
    assert list(cache.glob(".tmp-*")) == []


def test_publish_retries_os_replace_then_succeeds(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash9b"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=1.0)
    _install_stub_separator(monkeypatch, out_seconds=1.0)

    import guitar_helper.analysis.source_separator as mod

    real_replace = mod.os.replace
    calls = {"n": 0}

    def _flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("simulated: destination open")
        return real_replace(src, dst)

    monkeypatch.setattr(mod.os, "replace", _flaky_replace)

    sep = AudioSeparator(cache_dir=cache)
    result = sep.separate_guitar(source, file_hash)

    assert result == cache / f"{file_hash}_guitar.wav"
    assert calls["n"] >= 2


def test_publish_falls_back_to_existing_valid_cache(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash9c"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=1.0)

    sep = AudioSeparator(cache_dir=cache)
    cached = cache / f"{file_hash}_guitar.wav"
    manifest_path = cache / f"{file_hash}_guitar.json"

    # Establish a genuinely valid cache entry up front, as if another process won the race.
    _write_wav(cached, seconds=1.0)
    manifest = sep._build_manifest(cached, file_hash, sep._source_duration_ms(source), grandfathered=False)
    sep._publish_json(manifest, manifest_path)

    _install_stub_separator(monkeypatch, out_seconds=1.0)

    import guitar_helper.analysis.source_separator as mod

    def _always_fail(src, dst):
        raise PermissionError("simulated: destination permanently open")

    monkeypatch.setattr(mod.os, "replace", _always_fail)

    result = sep._perform_separation(source, file_hash, cached, manifest_path)
    assert result == cached


# ---------------------------------------------------------------------------
# 10. Post-write validation blocks a truncated separation
# ---------------------------------------------------------------------------


def test_post_write_validation_rejects_truncated_output(tmp_path, monkeypatch):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash10"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=4.0)
    _install_stub_separator(monkeypatch, out_seconds=2.0)  # half the source duration

    sep = AudioSeparator(cache_dir=cache)
    with pytest.raises(SeparationError):
        sep.separate_guitar(source, file_hash)

    assert not (cache / f"{file_hash}_guitar.wav").exists()
    assert not (cache / f"{file_hash}_guitar.json").exists()
    assert list(cache.glob(".tmp-*")) == []


# ---------------------------------------------------------------------------
# 11. Manifest is written after the wav, and atomically
# ---------------------------------------------------------------------------


def test_manifest_publish_failure_is_non_fatal_and_self_heals(tmp_path, monkeypatch):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash11"
    source = tmp_path / "source.wav"
    _write_wav(source, seconds=1.0)
    _install_stub_separator(monkeypatch, out_seconds=1.0)

    sep = AudioSeparator(cache_dir=cache)

    def _raise(*_a, **_k):
        raise OSError("simulated manifest publish failure")

    monkeypatch.setattr(sep, "_publish_json", _raise)

    result = sep.separate_guitar(source, file_hash)

    cached = cache / f"{file_hash}_guitar.wav"
    assert result == cached
    assert cached.exists()
    assert sf.info(cached).frames > 0
    assert not (cache / f"{file_hash}_guitar.json").exists()

    monkeypatch.undo()
    assert sep._check_cache(cached, source, file_hash) is None
    manifest = json.loads((cache / f"{file_hash}_guitar.json").read_text())
    assert manifest["grandfathered"] is True


# ---------------------------------------------------------------------------
# Additional invariants
# ---------------------------------------------------------------------------


def test_sample_digest_deterministic(tmp_path):
    wav = tmp_path / "a.wav"
    _write_wav(wav, seconds=2.0)
    size = wav.stat().st_size
    d1, s1 = _sample_digest(wav, size)
    d2, s2 = _sample_digest(wav, size)
    assert d1 == d2
    assert s1 == s2


def test_sample_digest_small_file_reads_whole_file(tmp_path):
    small = tmp_path / "small.bin"
    small.write_bytes(b"abc123" * 10)
    size = small.stat().st_size
    digest, sampled = _sample_digest(small, size)
    assert len(sampled) == size
    assert digest


def test_manifest_missing_required_key_is_manifest_unreadable(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash-missing-key"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=1.0)
    manifest = {
        "cache_format_version": 1,
        "source_hash": file_hash,
        # stem_bytes deliberately omitted
        "stem_frames": 1,
        "stem_samplerate": 1,
        "stem_sample_digest": "x",
        "model": "htdemucs_6s.yaml",
        "model_files": None,
        "source_duration_ms": None,
    }
    (cache / f"{file_hash}_guitar.json").write_text(json.dumps(manifest))

    sep = AudioSeparator(cache_dir=cache)
    assert sep._check_cache(cached, tmp_path / "source.wav", file_hash) == "manifest-unreadable"


def test_manifest_unparseable_json_is_manifest_unreadable(tmp_path):
    cache = tmp_path / "stems"
    cache.mkdir()
    file_hash = "hash-bad-json"
    cached = cache / f"{file_hash}_guitar.wav"
    _write_wav(cached, seconds=1.0)
    (cache / f"{file_hash}_guitar.json").write_text("{not json")

    sep = AudioSeparator(cache_dir=cache)
    assert sep._check_cache(cached, tmp_path / "source.wav", file_hash) == "manifest-unreadable"


def test_null_separator_docstring_warns_test_only():
    doc = NullSeparator.__doc__ or ""
    assert "TEST AND FIXTURE USE ONLY" in doc
    assert "0.463" in doc and "0.821" in doc


def test_separate_guitar_signature_frozen():
    import inspect

    sig = inspect.signature(AudioSeparator.separate_guitar)
    params = list(sig.parameters)
    assert params == ["self", "path", "file_hash"]


# --- bundled model resolution (packaging) ------------------------------------


def test_default_model_dir_uses_the_tmp_default_from_a_checkout(monkeypatch):
    import guitar_helper.analysis.source_separator as sep

    monkeypatch.setattr(sep, "is_frozen", lambda: False)
    assert str(sep.default_model_dir()) == str(Path(sep._DEFAULT_MODEL_DIR))


def test_default_model_dir_uses_the_bundle_when_frozen(monkeypatch, tmp_path):
    import guitar_helper.analysis.source_separator as sep

    monkeypatch.setattr(sep, "is_frozen", lambda: True)
    monkeypatch.setattr(sep, "resource_path", lambda name: tmp_path / name)
    # /tmp is both wrong on Windows and a directory cleanup tools delete, so a
    # frozen build must never fall back to it.
    assert sep.default_model_dir() == tmp_path / "models"


def test_explicit_model_dir_still_beats_the_frozen_default(monkeypatch, tmp_path):
    import guitar_helper.analysis.source_separator as sep

    monkeypatch.setattr(sep, "is_frozen", lambda: True)
    monkeypatch.setattr(sep, "resource_path", lambda name: tmp_path / "bundle" / name)
    separator = sep.AudioSeparator(cache_dir=str(tmp_path / "stems"), model_dir=str(tmp_path / "mine"))
    assert separator._resolve_model_dir() == tmp_path / "mine"
