from __future__ import annotations

import importlib.util

from guitar_helper.analysis import environment
from guitar_helper.analysis.environment import (
    Severity,
    check_ffmpeg,
    check_files,
    check_model_weights,
    check_separation_stack,
    check_writable,
    preflight,
)

# ---------------------------------------------------------------------------
# D13 — separation stack check
# ---------------------------------------------------------------------------

def test_separation_stack_blocks_when_a_module_is_missing(monkeypatch):
    def _find_spec(name):
        return None if name == "audio_separator" else object()

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)

    check = check_separation_stack()

    assert check.ok is False
    assert check.severity is Severity.BLOCK


def test_separation_stack_calls_find_spec_for_every_module(monkeypatch):
    calls: list[str] = []

    def _find_spec(name):
        calls.append(name)
        return object()

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)

    check = check_separation_stack()
    assert check.ok is True
    assert set(environment._SEPARATION_MODULES) <= set(calls)

    calls.clear()

    def _find_spec_missing(name):
        calls.append(name)
        return None if name == "torch" else object()

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec_missing)

    check = check_separation_stack()
    assert check.ok is False


# ---------------------------------------------------------------------------
# D12 — preflight assembles a single report
# ---------------------------------------------------------------------------

def test_preflight_clean_environment_one_wav(tmp_path, monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(environment.shutil, "which", lambda name: "C:/ffmpeg/ffmpeg.exe")
    monkeypatch.setattr(environment, "_is_writable", lambda directory: (True, ""))

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / environment._MODEL_CONFIG).write_bytes(b"x" * 21)
    (model_dir / "weights.th").write_bytes(b"0" * environment._MIN_WEIGHTS_BYTES)

    wav = tmp_path / "song.wav"
    wav.write_bytes(b"RIFF....")

    report = preflight(
        [wav],
        stems_dir=tmp_path / "stems",
        db_path=tmp_path / "library.db",
        model_dir=model_dir,
    )

    assert report.blockers == []
    assert report.warnings == []
    assert report.can_proceed is True
    assert report.usable_files == [wav]


# ---------------------------------------------------------------------------
# ffmpeg is a BLOCK (reverses the earlier "WARN + per-file rejection" rule).
# build_pipeline always uses AudioSeparator, and Separator.__init__ calls
# check_ffmpeg_installed(), which raises when ffmpeg is absent — so nothing can
# be analysed without it, .wav included. The per-file rejection still applies
# on top, to say which files also need ffmpeg merely to decode.
# ---------------------------------------------------------------------------

def test_ffmpeg_absent_blocks_ingestion(tmp_path, monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    monkeypatch.setattr(environment, "_is_writable", lambda directory: (True, ""))

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / environment._MODEL_CONFIG).write_bytes(b"x" * 21)
    (model_dir / "weights.th").write_bytes(b"0" * environment._MIN_WEIGHTS_BYTES)

    wav = tmp_path / "song.wav"
    wav.write_bytes(b"RIFF....")
    m4a = tmp_path / "song.m4a"
    m4a.write_bytes(b"....")

    ffmpeg_check = check_ffmpeg()
    assert ffmpeg_check.severity is Severity.BLOCK
    assert ffmpeg_check.ok is False
    assert "winget install" in ffmpeg_check.remedy

    report = preflight([wav, m4a], stems_dir=tmp_path / "stems", db_path=tmp_path / "library.db", model_dir=model_dir)

    # Even the natively-decodable .wav cannot proceed: separation is mandatory
    # and the separator will not construct without ffmpeg.
    assert report.can_proceed is False
    assert [c.name for c in report.blockers] == ["ffmpeg"]

    # The per-file verdict is unchanged — .wav still decodes, .m4a still needs
    # ffmpeg to decode. The blocker is about separation, not about decoding.
    file_codes = {f.path.name: f.code for f in report.files}
    assert file_codes["song.wav"] == "ok"
    assert file_codes["song.m4a"] == "needs_ffmpeg"


def test_ffmpeg_present_m4a_ok(tmp_path):
    m4a = tmp_path / "song.m4a"
    m4a.write_bytes(b"....")
    checks = check_files([m4a], ffmpeg_available=True)
    assert checks[0].code == "ok"


# ---------------------------------------------------------------------------
# D14 — model weights check is presence-only and BLOCKs
# ---------------------------------------------------------------------------

def test_model_weights_empty_dir_blocks(tmp_path):
    check = check_model_weights(tmp_path / "empty")
    assert check.ok is False
    assert check.severity is Severity.BLOCK


def test_model_weights_config_without_th_blocks(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / environment._MODEL_CONFIG).write_bytes(b"x" * 21)
    check = check_model_weights(model_dir)
    assert check.ok is False


def test_model_weights_th_below_floor_blocks(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / environment._MODEL_CONFIG).write_bytes(b"x" * 21)
    (model_dir / "weights.th").write_bytes(b"0" * 1024)
    check = check_model_weights(model_dir)
    assert check.ok is False


def test_model_weights_all_present_passes(tmp_path):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / environment._MODEL_CONFIG).write_bytes(b"x" * 21)
    (model_dir / "weights.th").write_bytes(b"0" * environment._MIN_WEIGHTS_BYTES)
    check = check_model_weights(model_dir)
    assert check.ok is True


def test_model_weights_default_dir_resolved_via_module_constant(tmp_path, monkeypatch):
    # Shared with AudioSeparator._resolve_model_dir so the preflight check and
    # the separator can never disagree about where the weights live.
    monkeypatch.setattr(environment, "default_model_dir", lambda: tmp_path)
    (tmp_path / environment._MODEL_CONFIG).write_bytes(b"x" * 21)
    (tmp_path / "weights.th").write_bytes(b"0" * environment._MIN_WEIGHTS_BYTES)

    check = check_model_weights(None)

    assert check.ok is True


# ---------------------------------------------------------------------------
# D17 — writability
# ---------------------------------------------------------------------------

def test_writability_failure_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr(environment, "_is_writable", lambda directory: (False, "denied"))

    stem_check = check_writable(tmp_path / "stems", name="stem_cache_writable")
    db_check = check_writable(tmp_path / "db", name="database_writable")

    assert stem_check.ok is False
    assert stem_check.severity is Severity.BLOCK
    assert db_check.ok is False
    assert db_check.severity is Severity.BLOCK


def test_preflight_creates_missing_stems_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(environment.shutil, "which", lambda name: "ffmpeg")

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / environment._MODEL_CONFIG).write_bytes(b"x" * 21)
    (model_dir / "weights.th").write_bytes(b"0" * environment._MIN_WEIGHTS_BYTES)

    stems_dir = tmp_path / "brand_new_stems"
    assert not stems_dir.exists()

    report = preflight([], stems_dir=stems_dir, db_path=tmp_path / "library.db", model_dir=model_dir)

    stem_check = next(c for c in report.checks if c.name == "stem_cache_writable")
    assert stem_check.ok is True
    assert stems_dir.exists()


# ---------------------------------------------------------------------------
# Per-file codes
# ---------------------------------------------------------------------------

def test_file_codes(tmp_path, monkeypatch):
    missing = tmp_path / "ghost.wav"
    a_dir = tmp_path / "a_directory"
    a_dir.mkdir()
    notes = tmp_path / "notes.txt"
    notes.write_text("hi")
    unreadable = tmp_path / "broken.wav"
    unreadable.write_bytes(b"RIFF")

    real_open = open

    def _raising_open(path, *args, **kwargs):
        if str(path) == str(unreadable):
            raise OSError("simulated read failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(environment, "open", _raising_open, raising=False)

    checks = check_files([missing, a_dir, notes, unreadable], ffmpeg_available=True)
    codes = {str(c.path): c.code for c in checks}

    assert codes[str(missing)] == "missing"
    assert codes[str(a_dir)] == "missing"
    assert codes[str(notes)] == "unsupported_suffix"
    assert codes[str(unreadable)] == "unreadable"


# ---------------------------------------------------------------------------
# D12 — zero usable files still blocks proceeding
# ---------------------------------------------------------------------------

def test_zero_usable_files_cannot_proceed(tmp_path, monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(environment.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.setattr(environment, "_is_writable", lambda directory: (True, ""))

    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / environment._MODEL_CONFIG).write_bytes(b"x" * 21)
    (model_dir / "weights.th").write_bytes(b"0" * environment._MIN_WEIGHTS_BYTES)

    report = preflight([], stems_dir=tmp_path / "stems", db_path=tmp_path / "library.db", model_dir=model_dir)

    assert report.blockers == []
    assert report.can_proceed is False
