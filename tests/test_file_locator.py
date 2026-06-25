from __future__ import annotations

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.analysis.file_locator import locate


def _hash(path) -> str:
    return AudioLoader.hash_file(path)


# Test 1 — existing source_path is returned without scanning
def test_locate_uses_existing_source_path(make_wav):
    wav = make_wav("a.wav")
    result = locate(_hash(wav), str(wav), library_root=None)
    assert result == wav


# Test 2 — moved file is re-located by content hash under the library root
def test_locate_relocates_by_hash(make_wav, tmp_path):
    wav = make_wav("a.wav")
    target = _hash(wav)
    moved = tmp_path / "subdir" / "renamed.wav"
    moved.parent.mkdir()
    wav.rename(moved)
    result = locate(target, str(wav), library_root=tmp_path)
    assert result == moved


# Test 3 — basename collision: the hash-matching file wins, not the same-named one
def test_locate_basename_collision_picks_hash_match(make_wav, tmp_path):
    wanted = make_wav("wanted.wav", duration_s=2.0)
    decoy = make_wav("decoy.wav", duration_s=1.0)
    target = _hash(wanted)
    assert target != _hash(decoy)
    # Two files sharing the basename "intro.wav" in different folders.
    wanted_dir = tmp_path / "deep"
    decoy_dir = tmp_path / "other"
    wanted_dir.mkdir()
    decoy_dir.mkdir()
    moved = wanted_dir / "intro.wav"
    wanted.rename(moved)
    decoy.rename(decoy_dir / "intro.wav")
    # source_path points at the old location, which no longer exists.
    result = locate(target, str(wanted), library_root=tmp_path)
    assert result == moved
    assert _hash(result) == target


# Test 4 — no match returns None
def test_locate_no_match_returns_none(make_wav, tmp_path):
    wav = make_wav("a.wav")
    wav.unlink()
    result = locate("deadbeef", None, library_root=tmp_path)
    assert result is None


# Test 5 — missing library root returns None instead of raising
def test_locate_missing_root_returns_none():
    assert locate("deadbeef", None, library_root="/no/such/dir") is None
