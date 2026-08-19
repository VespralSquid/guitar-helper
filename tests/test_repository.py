import sqlite3

import pytest

from guitar_helper.db.interfaces import Preset, Segment
from guitar_helper.db.schema import default_preset_map
from tests.conftest import make_segment

# =========================================================================
# Presets
# =========================================================================

# Test 8
def test_get_presets_returns_all_defaults(store):
    presets = store.get_presets()
    assert len(presets) == 6
    labels = {p.tone_label for p in presets}
    assert labels == {"clean", "edge", "overdrive", "crunch", "metal", "other"}
    assert "ambient" not in labels
    by_label = {p.tone_label: p for p in presets}
    assert by_label["clean"].pc_number == 0
    assert by_label["edge"].pc_number == 1
    assert by_label["overdrive"].pc_number == 2
    assert by_label["crunch"].pc_number == 3
    assert by_label["metal"].pc_number == 4
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
    assert len(presets) == 7
    labels = {p.tone_label for p in presets}
    assert "lead" in labels


# Test 11
def test_save_preset_upsert_updates_existing(store):
    store.save_preset(Preset(tone_label="clean", preset_name="Clean Lead", pc_number=99))
    presets = store.get_presets()
    assert len(presets) == 6  # no new row
    by_label = {p.tone_label: p for p in presets}
    assert by_label["clean"].pc_number == 99
    assert by_label["clean"].preset_name == "Clean Lead"


# ISSUE-006 — user_modified flag + reset to defaults
# =========================================================================

def test_save_preset_sets_user_modified_flag(store, db):
    store.save_preset(Preset(tone_label="clean", preset_name="Clean", pc_number=7))
    rows = dict(db.execute("SELECT tone_label, user_modified FROM presets").fetchall())
    assert rows["clean"] == 1
    assert all(flag == 0 for tone, flag in rows.items() if tone != "clean")


def test_save_preset_name_only_edit_also_sets_the_flag(store, db):
    store.save_preset(Preset(tone_label="metal", preset_name="Rectifier", pc_number=4))
    flag = db.execute(
        "SELECT user_modified FROM presets WHERE tone_label = 'metal'"
    ).fetchone()[0]
    assert flag == 1


def test_reset_presets_to_defaults_restores_pcs_and_clears_flags(store, db):
    store.save_preset(Preset(tone_label="clean", preset_name="Clean", pc_number=99))
    store.save_preset(Preset(tone_label="metal", preset_name="Rectifier", pc_number=50))

    store.reset_presets_to_defaults()

    presets = {p.tone_label: p.pc_number for p in store.get_presets()}
    assert presets == default_preset_map()
    flags = {r[0] for r in db.execute("SELECT user_modified FROM presets").fetchall()}
    assert flags == {0}
    dupes = db.execute(
        "SELECT pc_number FROM presets WHERE pc_number >= 0 "
        "GROUP BY pc_number HAVING COUNT(*) > 1"
    ).fetchall()
    assert dupes == []


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
        make_segment(track_hash, 1000, 2000, tone_label="clean"),
    ])
    results = store.get_segments(track_hash)
    assert len(results) == 2
    assert results[0].tone_label == "metal"
    assert results[1].tone_label == "clean"


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
# delete_segment
# =========================================================================

def test_delete_segment_removes_row(store, track_hash):
    store.save_segments(track_hash, [
        make_segment(track_hash, 0, 1000),
        make_segment(track_hash, 1000, 2000),
    ])
    victim, survivor = store.get_segments(track_hash)
    store.delete_segment(victim.id)
    remaining = store.get_segments(track_hash)
    assert len(remaining) == 1
    assert remaining[0].id == survivor.id


def test_delete_segment_bad_id_no_exception(store):
    store.delete_segment(999999)  # must not raise


# =========================================================================
# ensure_calibration_copy / get_calibration_segments
# =========================================================================

def test_get_calibration_segments_empty_when_never_copied(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    assert store.get_calibration_segments(track_hash) == []


def test_ensure_calibration_copy_snapshots_current_segments(store, track_hash):
    store.save_segments(track_hash, [
        make_segment(track_hash, 0, 1000, tone_label="clean"),
        make_segment(track_hash, 1000, 2000, tone_label="metal"),
    ])
    store.ensure_calibration_copy(track_hash)
    snapshot = store.get_calibration_segments(track_hash)
    assert [s.tone_label for s in snapshot] == ["clean", "metal"]


def test_ensure_calibration_copy_idempotent_preserves_first_snapshot(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000, tone_label="clean")])
    store.ensure_calibration_copy(track_hash)

    seg = store.get_segments(track_hash)[0]
    seg.tone_label = "metal"
    store.update_segment(seg)
    store.ensure_calibration_copy(track_hash)  # second call: must be a no-op

    snapshot = store.get_calibration_segments(track_hash)
    assert len(snapshot) == 1
    assert snapshot[0].tone_label == "clean"  # unaffected by the later edit
    assert store.get_segments(track_hash)[0].tone_label == "metal"  # edit still applied


def test_ensure_calibration_copy_no_segments_is_noop(store, track_hash):
    store.ensure_calibration_copy(track_hash)
    assert store.get_calibration_segments(track_hash) == []


# =========================================================================
# B1 — atomic writes (mvp-readiness-review §B1)
# =========================================================================

# Test 10
def test_save_segments_rolls_back_on_error(store, track_hash):
    store.save_segments(
        track_hash,
        [make_segment(track_hash, 0, 1000, tone_label="metal", manually_corrected=True)],
    )
    good = make_segment(track_hash, 1000, 2000, tone_label="clean")
    bad = make_segment(track_hash, 2000, 3000, tone_label="bogus")

    with pytest.raises(sqlite3.IntegrityError):
        store.save_segments(track_hash, [good, bad])

    remaining = store.get_segments(track_hash)
    assert len(remaining) == 1
    assert remaining[0].tone_label == "metal"
    assert remaining[0].manually_corrected is True

    # The pre-fix bug only became visible at the *next* commit — prove it stays fixed.
    store.set_setting("unrelated", "write")
    remaining_again = store.get_segments(track_hash)
    assert len(remaining_again) == 1
    assert remaining_again[0].manually_corrected is True


# Test 11
def test_apply_edits_rolls_back_the_whole_session(store, track_hash):
    store.save_segments(
        track_hash,
        [
            make_segment(track_hash, 0, 1000, tone_label="clean"),
            make_segment(track_hash, 1000, 2000, tone_label="metal"),
            make_segment(track_hash, 2000, 3000, tone_label="crunch"),
        ],
    )
    keep, victim, deleted = store.get_segments(track_hash)
    good_update = Segment(
        id=keep.id, file_hash=track_hash, start_ms=0, end_ms=900,
        tone_label="clean", confidence=0.9, manually_corrected=True,
    )
    bad_update = Segment(
        id=victim.id, file_hash=track_hash, start_ms=1000, end_ms=2000,
        tone_label="bogus", confidence=0.9, manually_corrected=True,
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.apply_edits(track_hash, [good_update, bad_update], [deleted.id])

    remaining = store.get_segments(track_hash)
    assert [s.tone_label for s in remaining] == ["clean", "metal", "crunch"]
    assert remaining[0].end_ms == 1000  # good_update did not survive either
    assert store.get_calibration_segments(track_hash) == []  # snapshot rolled back too


# Test 12
def test_apply_edits_happy_path_writes_and_snapshots_once(store, track_hash):
    store.save_segments(
        track_hash,
        [
            make_segment(track_hash, 0, 1000, tone_label="clean"),
            make_segment(track_hash, 1000, 2000, tone_label="metal"),
        ],
    )
    keep, drop = store.get_segments(track_hash)
    updated = Segment(
        id=keep.id, file_hash=track_hash, start_ms=0, end_ms=1000,
        tone_label="crunch", confidence=1.0, manually_corrected=True,
    )

    store.apply_edits(track_hash, [updated], [drop.id])

    remaining = store.get_segments(track_hash)
    assert len(remaining) == 1
    assert remaining[0].tone_label == "crunch"
    snapshot = store.get_calibration_segments(track_hash)
    assert [s.tone_label for s in snapshot] == ["clean", "metal"]

    # a second apply_edits must not overwrite the first snapshot
    updated_again = Segment(
        id=updated.id, file_hash=track_hash, start_ms=0, end_ms=1000,
        tone_label="overdrive", confidence=1.0, manually_corrected=True,
    )
    store.apply_edits(track_hash, [updated_again], [])
    snapshot_after_second_call = store.get_calibration_segments(track_hash)
    assert [s.tone_label for s in snapshot_after_second_call] == ["clean", "metal"]


# =========================================================================
# FK constraints
# =========================================================================

# Test 26
def test_save_segments_unknown_file_hash_raises(store):
    seg = make_segment("no_such_hash", 0, 5000)
    with pytest.raises(sqlite3.IntegrityError):
        store.save_segments("no_such_hash", [seg])


# =========================================================================
# save_track — source_path persistence
# =========================================================================

def _track_row(db, file_hash):
    return db.execute(
        "SELECT filename, title, artist, duration_ms, source_path, calibration_excluded "
        "FROM tracks WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()


# Test 27
def test_save_track_persists_source_path(store, db):
    store.save_track("h1", "song.wav", "Song", "Artist", 60000, source_path="/abs/song.wav")
    row = _track_row(db, "h1")
    assert row[0] == "song.wav"
    assert row[4] == "/abs/song.wav"


# Test 28 — re-analysis of a moved file refreshes the stored path (upsert, not no-op)
def test_save_track_upsert_updates_moved_path(store, db):
    store.save_track("h1", "song.wav", "Song", "Artist", 60000, source_path="/old/song.wav")
    store.save_track("h1", "song.wav", "Song", "Artist", 60000, source_path="/new/song.wav")
    rows = db.execute("SELECT COUNT(*) FROM tracks WHERE file_hash = 'h1'").fetchone()[0]
    assert rows == 1  # no duplicate row
    assert _track_row(db, "h1")[4] == "/new/song.wav"


# Test 29 — upsert must not clobber a user's calibration exclusion flag
def test_save_track_upsert_preserves_calibration_excluded(store, db):
    store.save_track("h1", "song.wav", None, None, 60000, source_path="/a.wav")
    store.set_calibration_excluded("h1", True)
    store.save_track("h1", "song.wav", None, None, 60000, source_path="/b.wav")
    assert _track_row(db, "h1")[5] == 1


# Test 30 — source_path is optional
def test_save_track_source_path_defaults_null(store, db):
    store.save_track("h1", "song.wav", None, None, 60000)
    assert _track_row(db, "h1")[4] is None


# =========================================================================
# list_tracks
# =========================================================================

# Test 31 — zero-segment track still appears (LEFT JOIN), with zero counts
def test_list_tracks_includes_zero_segment_track(store):
    store.save_track("h1", "song.wav", "Song", "Artist", 60000, source_path="/a.wav")
    tracks = store.list_tracks()
    assert len(tracks) == 1
    t = tracks[0]
    assert t.file_hash == "h1"
    assert t.corrected_count == 0
    assert t.total_count == 0
    assert t.calibration_excluded is False
    assert t.source_path == "/a.wav"


# Test 32 — corrected/total counts aggregate manually_corrected segments
def test_list_tracks_aggregates_correction_counts(store):
    store.save_track("h1", "song.wav", "Song", "Artist", 60000)
    store.save_segments(
        "h1",
        [
            make_segment("h1", 0, 1000, manually_corrected=True),
            make_segment("h1", 1000, 2000, manually_corrected=False),
            make_segment("h1", 2000, 3000, manually_corrected=True),
        ],
    )
    tracks = store.list_tracks()
    assert len(tracks) == 1
    assert tracks[0].corrected_count == 2
    assert tracks[0].total_count == 3


# Test 33 — ordered by artist, title, filename
def test_list_tracks_ordered_by_artist_title_filename(store):
    store.save_track("h2", "b.wav", "Song B", "Zed", 60000)
    store.save_track("h1", "a.wav", "Song A", "Abe", 60000)
    tracks = store.list_tracks()
    assert [t.file_hash for t in tracks] == ["h1", "h2"]


# Test 34 — no tracks in an empty DB
def test_list_tracks_empty(store):
    assert store.list_tracks() == []


# ------------------------------------------------------------------
# Playlists (O2)
# ------------------------------------------------------------------

def test_playlist_crud_roundtrip(store, track_hash):
    playlist_id = store.create_playlist("Practice")
    playlists = store.list_playlists()
    assert [p.name for p in playlists] == ["Practice"]
    assert playlists[0].track_count == 0

    store.add_to_playlist(playlist_id, track_hash)
    store.add_to_playlist(playlist_id, track_hash)  # duplicate is a no-op
    assert store.list_playlists()[0].track_count == 1
    assert [t.file_hash for t in store.get_playlist_tracks(playlist_id)] == [track_hash]

    store.remove_from_playlist(playlist_id, track_hash)
    assert store.get_playlist_tracks(playlist_id) == []

    store.delete_playlist(playlist_id)
    assert store.list_playlists() == []


def test_playlist_tracks_keep_insertion_order(store):
    for h, name in (("hc", "c.wav"), ("ha", "a.wav"), ("hb", "b.wav")):
        store.save_track(h, name, None, None, 1000)
    playlist_id = store.create_playlist("Ordered")
    for h in ("hc", "ha", "hb"):
        store.add_to_playlist(playlist_id, h)
    assert [t.file_hash for t in store.get_playlist_tracks(playlist_id)] == ["hc", "ha", "hb"]


def test_delete_playlist_cascades_membership(store, db, track_hash):
    playlist_id = store.create_playlist("Doomed")
    store.add_to_playlist(playlist_id, track_hash)
    store.delete_playlist(playlist_id)
    rows = db.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0]
    assert rows == 0


def test_duplicate_playlist_name_raises(store):
    store.create_playlist("Same")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_playlist("Same")


def test_list_tracks_includes_analysed_at(store, track_hash):
    track = store.list_tracks()[0]
    assert track.analysed_at.startswith("2026-01-01")


# ----------------------------------------------------------------------
# Settings (schema v9, O4)
# ----------------------------------------------------------------------

def test_setting_roundtrip(store):
    store.set_setting("dispatch_offset_ms", "40")
    assert store.get_setting("dispatch_offset_ms") == "40"


def test_get_setting_returns_none_when_unset(store):
    assert store.get_setting("never_written") is None


def test_set_setting_overwrites(store):
    store.set_setting("dispatch_offset_ms", "40")
    store.set_setting("dispatch_offset_ms", "-15")
    assert store.get_int_setting("dispatch_offset_ms", 75) == -15


def test_get_int_setting_uses_default_when_unset(store):
    assert store.get_int_setting("dispatch_offset_ms", 75) == 75


def test_get_int_setting_falls_back_on_garbage(store):
    """A hand-edited DB must not stop the app from starting."""
    store.set_setting("dispatch_offset_ms", "not-a-number")
    assert store.get_int_setting("dispatch_offset_ms", 75) == 75


# ----------------------------------------------------------------------
# Track deletion (MVP §4.3 — remove-and-re-add)
# ----------------------------------------------------------------------

def _seed_track(store, db, file_hash: str, filename: str):
    store.save_track(file_hash, filename, None, None, 60000)
    store.save_segments(
        file_hash,
        [
            make_segment(file_hash, 0, 1000, tone_label="clean"),
            make_segment(file_hash, 1000, 2000, tone_label="metal"),
        ],
    )
    store.ensure_calibration_copy(file_hash)
    playlist_id = store.create_playlist(f"pl-{file_hash}")
    store.add_to_playlist(playlist_id, file_hash)
    return playlist_id


def _counts(db, file_hash: str) -> dict[str, int]:
    def count(sql: str) -> int:
        return db.execute(sql, (file_hash,)).fetchone()[0]

    return {
        "segments": count("SELECT COUNT(*) FROM segments WHERE file_hash = ?"),
        "segments_calibration": count(
            "SELECT COUNT(*) FROM segments_calibration WHERE file_hash = ?"
        ),
        "playlist_tracks": count(
            "SELECT COUNT(*) FROM playlist_tracks WHERE file_hash = ?"
        ),
        "tracks": count("SELECT COUNT(*) FROM tracks WHERE file_hash = ?"),
    }


def test_foreign_keys_are_enforced_on_the_test_connection(db):
    """Without this the FK-ordering tests below would pass vacuously."""
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_delete_track_clears_every_referencing_row(store, db):
    _seed_track(store, db, "doomed", "doomed.wav")
    assert _counts(db, "doomed") == {
        "segments": 2, "segments_calibration": 2, "playlist_tracks": 1, "tracks": 1,
    }

    store.delete_track("doomed")

    assert _counts(db, "doomed") == {
        "segments": 0, "segments_calibration": 0, "playlist_tracks": 0, "tracks": 0,
    }
    assert store.list_tracks() == []


def test_delete_track_in_a_playlist_does_not_raise_foreign_key_error(store, db):
    """The regression this design exists for: playlist_tracks.file_hash has no
    ON DELETE CASCADE, so deleting the tracks row first raises IntegrityError."""
    playlist_id = _seed_track(store, db, "member", "member.wav")

    store.delete_track("member")

    assert store.get_playlist_tracks(playlist_id) == []
    assert store.list_playlists()[0].track_count == 0


def test_delete_track_is_atomic(store, db):
    _seed_track(store, db, "victim", "victim.wav")
    db.execute(
        """
        CREATE TRIGGER block_track_delete BEFORE DELETE ON tracks
        BEGIN SELECT RAISE(ABORT, 'no'); END
        """
    )
    db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        store.delete_track("victim")

    assert _counts(db, "victim") == {
        "segments": 2, "segments_calibration": 2, "playlist_tracks": 1, "tracks": 1,
    }
    # The pre-fix failure mode surfaced only at the next commit — prove it stays fixed.
    store.set_setting("unrelated", "write")
    assert _counts(db, "victim")["segments"] == 2


def test_delete_track_unknown_hash_is_a_noop(store, db):
    _seed_track(store, db, "keeper", "keeper.wav")

    store.delete_track("never-analysed")

    assert _counts(db, "keeper") == {
        "segments": 2, "segments_calibration": 2, "playlist_tracks": 1, "tracks": 1,
    }


def test_delete_track_leaves_other_tracks_untouched(store, db):
    _seed_track(store, db, "doomed", "doomed.wav")
    survivor_playlist = _seed_track(store, db, "survivor", "survivor.wav")
    store.add_to_playlist(survivor_playlist, "doomed")

    store.delete_track("doomed")

    assert _counts(db, "survivor") == {
        "segments": 2, "segments_calibration": 2, "playlist_tracks": 1, "tracks": 1,
    }
    assert [t.file_hash for t in store.list_tracks()] == ["survivor"]
    assert [t.file_hash for t in store.get_playlist_tracks(survivor_playlist)] == [
        "survivor"
    ]
