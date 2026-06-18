import sqlite3

import pytest

from guitar_helper.db.interfaces import Preset, Segment
from tests.conftest import make_segment


# =========================================================================
# Presets
# =========================================================================

# Test 8
def test_get_presets_returns_all_defaults(store):
    presets = store.get_presets()
    assert len(presets) == 5
    labels = {p.tone_label for p in presets}
    assert labels == {"clean", "crunch", "metal", "ambient", "other"}
    by_label = {p.tone_label: p for p in presets}
    assert by_label["clean"].pc_number == 0
    assert by_label["crunch"].pc_number == 1
    assert by_label["metal"].pc_number == 2
    assert by_label["ambient"].pc_number == 3
    assert by_label["other"].pc_number == -1


# Test 9 — regression guard: ORDER BY pc_number ASC puts other (-1) first
def test_get_presets_order(store):
    presets = store.get_presets()
    assert presets[0].pc_number == -1
    assert presets[0].tone_label == "other"


# Test 10
def test_save_preset_insert_new(store):
    store.save_preset(Preset(tone_label="lead", preset_name="Lead Tone", pc_number=10))
    presets = store.get_presets()
    assert len(presets) == 6
    labels = {p.tone_label for p in presets}
    assert "lead" in labels


# Test 11
def test_save_preset_upsert_updates_existing(store):
    store.save_preset(Preset(tone_label="clean", preset_name="Clean Lead", pc_number=99))
    presets = store.get_presets()
    assert len(presets) == 5  # no new row
    by_label = {p.tone_label: p for p in presets}
    assert by_label["clean"].pc_number == 99
    assert by_label["clean"].preset_name == "Clean Lead"


# =========================================================================
# Segments — save / retrieve
# =========================================================================

# Test 12
def test_save_and_get_segments_roundtrip(store, track_hash):
    seg = make_segment(track_hash, 0, 5000, tone_label="crunch", confidence=0.85)
    store.save_segments(track_hash, [seg])
    result = store.get_segments(track_hash)
    assert len(result) == 1
    s = result[0]
    assert s.id != 0  # auto-assigned
    assert s.file_hash == track_hash
    assert s.start_ms == 0
    assert s.end_ms == 5000
    assert s.tone_label == "crunch"
    assert abs(s.confidence - 0.85) < 1e-9
    assert s.manually_corrected is False


# Test 13
def test_manually_corrected_bool_roundtrip(store, track_hash):
    true_seg = make_segment(track_hash, 0, 1000, manually_corrected=True)
    false_seg = make_segment(track_hash, 1000, 2000, manually_corrected=False)
    store.save_segments(track_hash, [true_seg, false_seg])
    results = store.get_segments(track_hash)
    assert results[0].manually_corrected is True
    assert results[1].manually_corrected is False


# Test 14
def test_save_segments_replaces_existing(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 3000)])
    store.save_segments(track_hash, [
        make_segment(track_hash, 0, 1000, tone_label="metal"),
        make_segment(track_hash, 1000, 2000, tone_label="ambient"),
    ])
    results = store.get_segments(track_hash)
    assert len(results) == 2
    assert results[0].tone_label == "metal"
    assert results[1].tone_label == "ambient"


# Test 15
def test_save_segments_empty_list_clears(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 5000)])
    store.save_segments(track_hash, [])
    assert store.get_segments(track_hash) == []


# Test 16
def test_get_segments_returns_ordered_by_start_ms(store, track_hash):
    store.save_segments(track_hash, [
        make_segment(track_hash, 2000, 3000),
        make_segment(track_hash, 0, 1000),
        make_segment(track_hash, 1000, 2000),
    ])
    results = store.get_segments(track_hash)
    starts = [s.start_ms for s in results]
    assert starts == sorted(starts)


# Test 17
def test_get_segments_unknown_hash_returns_empty(store):
    assert store.get_segments("nonexistent_hash") == []


# =========================================================================
# get_segment — point-in-time lookup
# =========================================================================

# Test 18
def test_get_segment_at_midpoint(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 10000)])
    result = store.get_segment(track_hash, 5000)
    assert result is not None
    assert result.start_ms == 0
    assert result.end_ms == 10000


# Test 19
def test_get_segment_at_start_ms_inclusive(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 1000, 5000)])
    assert store.get_segment(track_hash, 1000) is not None


# Test 20
def test_get_segment_at_end_ms_exclusive(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 1000, 5000)])
    assert store.get_segment(track_hash, 5000) is None


# Test 21
def test_get_segment_in_gap_returns_none(store, track_hash):
    store.save_segments(track_hash, [
        make_segment(track_hash, 0, 2000),
        make_segment(track_hash, 3000, 5000),
    ])
    assert store.get_segment(track_hash, 2500) is None


# Test 22
def test_get_segment_multiple_tracks_no_crossover(store, db):
    db.execute(
        "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at) VALUES (?,?,?,?)",
        ("hash_a", "a.wav", 10000, "2026-01-01T00:00:00+00:00"),
    )
    db.execute(
        "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at) VALUES (?,?,?,?)",
        ("hash_b", "b.wav", 10000, "2026-01-01T00:00:00+00:00"),
    )
    db.commit()
    store.save_segments("hash_a", [make_segment("hash_a", 0, 5000, tone_label="clean")])
    store.save_segments("hash_b", [make_segment("hash_b", 0, 5000, tone_label="metal")])
    a_result = store.get_segment("hash_a", 2500)
    b_result = store.get_segment("hash_b", 2500)
    assert a_result is not None and a_result.tone_label == "clean"
    assert b_result is not None and b_result.tone_label == "metal"


# Test 23
def test_get_segment_no_segments_returns_none(store, track_hash):
    assert store.get_segment(track_hash, 0) is None


# =========================================================================
# update_segment
# =========================================================================

# Test 24
def test_update_segment_all_fields(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 5000)])
    seg = store.get_segments(track_hash)[0]
    seg.start_ms = 100
    seg.end_ms = 4900
    seg.tone_label = "metal"
    seg.confidence = 0.42
    seg.manually_corrected = True
    store.update_segment(seg)
    updated = store.get_segments(track_hash)[0]
    assert updated.start_ms == 100
    assert updated.end_ms == 4900
    assert updated.tone_label == "metal"
    assert abs(updated.confidence - 0.42) < 1e-9
    assert updated.manually_corrected is True


# Test 25 — documents the silent no-op behavior for an invalid ID
def test_update_segment_bad_id_no_exception(store, track_hash):
    phantom = Segment(
        id=999999,
        file_hash=track_hash,
        start_ms=0,
        end_ms=1000,
        tone_label="clean",
        confidence=0.5,
        manually_corrected=False,
    )
    store.update_segment(phantom)  # must not raise


# =========================================================================
# FK constraints
# =========================================================================

# Test 26
def test_save_segments_unknown_file_hash_raises(store):
    seg = make_segment("no_such_hash", 0, 5000)
    with pytest.raises(sqlite3.IntegrityError):
        store.save_segments("no_such_hash", [seg])
