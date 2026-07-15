from __future__ import annotations

from guitar_helper.ui.state.editor_state import EditorState, StateEvent
from tests.conftest import make_segment

TONE_LABELS = ("clean", "crunch", "metal", "edge", "overdrive", "other")


def _state(store) -> EditorState:
    return EditorState(store, TONE_LABELS)


def test_load_track_populates_segments_and_resets_selection(store, track_hash):
    store.save_segments(
        track_hash,
        [make_segment(track_hash, 0, 1000), make_segment(track_hash, 1000, 2000, "metal")],
    )
    state = _state(store)
    state.load_track(track_hash)

    assert state.file_hash == track_hash
    assert len(state.segments) == 2
    assert state.selection_index is None


def test_load_track_emits_loaded_event(store, track_hash):
    events: list[StateEvent] = []
    state = _state(store)
    state.subscribe(events.append)

    state.load_track(track_hash)

    assert [e.kind for e in events] == ["loaded"]


def test_segments_property_is_a_defensive_copy(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)

    snapshot = state.segments
    snapshot.append(make_segment(track_hash, 1000, 2000))

    assert len(state.segments) == 1


def test_clear_resets_state_and_emits_loaded(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)
    state.select(0)
    events: list[StateEvent] = []
    state.subscribe(events.append)

    state.clear()

    assert state.file_hash is None
    assert state.segments == []
    assert state.selection_index is None
    assert [e.kind for e in events] == ["loaded"]


def test_select_updates_index_and_emits_selection(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)
    events: list[StateEvent] = []
    state.subscribe(events.append)

    state.select(0)

    assert state.selection_index == 0
    assert [e.kind for e in events] == ["selection"]


def test_select_same_index_is_a_no_op(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)
    state.select(0)
    events: list[StateEvent] = []
    state.subscribe(events.append)

    state.select(0)

    assert events == []


def test_select_out_of_range_index_is_ignored(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)

    state.select(5)

    assert state.selection_index is None


def test_select_none_clears_selection(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)
    state.select(0)

    state.select(None)

    assert state.selection_index is None


def test_segment_at_finds_covering_segment(store, track_hash):
    store.save_segments(
        track_hash,
        [
            make_segment(track_hash, 0, 1000, "clean"),
            make_segment(track_hash, 1000, 2000, "metal"),
        ],
    )
    state = _state(store)
    state.load_track(track_hash)

    assert state.segment_at(500) == 0
    assert state.segment_at(1500) == 1
    assert state.segment_at(999) == 0
    assert state.segment_at(1000) == 1


def test_segment_at_returns_none_outside_any_segment(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 1000, 2000)])
    state = _state(store)
    state.load_track(track_hash)

    assert state.segment_at(500) is None
    assert state.segment_at(2500) is None


def test_segment_at_with_no_segments_returns_none(store, track_hash):
    state = _state(store)
    state.load_track(track_hash)

    assert state.segment_at(0) is None


# =========================================================================
# relabel
# =========================================================================

def test_relabel_valid_label_mutates_and_marks_dirty(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000, "clean")])
    state = _state(store)
    state.load_track(track_hash)

    result = state.relabel(0, "metal")

    assert result.ok
    assert state.segments[0].tone_label == "metal"
    assert state.segments[0].manually_corrected is True
    assert state.dirty is True


def test_relabel_unknown_label_rejected_no_mutation(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000, "clean")])
    state = _state(store)
    state.load_track(track_hash)

    result = state.relabel(0, "fuzz")

    assert not result.ok
    assert state.segments[0].tone_label == "clean"
    assert state.dirty is False


def test_relabel_out_of_range_index_rejected(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)

    result = state.relabel(5, "metal")

    assert not result.ok
    assert state.dirty is False


# =========================================================================
# edit_boundary
# =========================================================================

def test_edit_boundary_valid_edit_keeps_label(store, track_hash):
    store.save_segments(
        track_hash,
        [make_segment(track_hash, 0, 1000, "crunch"), make_segment(track_hash, 1000, 2000)],
    )
    state = _state(store)
    state.load_track(track_hash)

    result = state.edit_boundary(0, 0, 900)

    assert result.ok
    assert state.segments[0].end_ms == 900
    assert state.segments[0].tone_label == "crunch"
    assert state.dirty is True


def test_edit_boundary_overlap_rejected_no_mutation(store, track_hash):
    store.save_segments(
        track_hash,
        [make_segment(track_hash, 0, 1000), make_segment(track_hash, 1000, 2000)],
    )
    state = _state(store)
    state.load_track(track_hash)

    result = state.edit_boundary(0, 0, 1500)

    assert not result.ok
    assert state.segments[0].end_ms == 1000
    assert state.dirty is False


def test_edit_boundary_out_of_range_index_rejected(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)

    result = state.edit_boundary(5, 0, 900)

    assert not result.ok


# =========================================================================
# move_boundary
# =========================================================================

def test_move_boundary_valid_move_updates_both_sides_contiguously(store, track_hash):
    store.save_segments(
        track_hash,
        [make_segment(track_hash, 0, 1000, "clean"), make_segment(track_hash, 1000, 2000, "metal")],
    )
    state = _state(store)
    state.load_track(track_hash)

    result = state.move_boundary(0, 1300)

    assert result.ok
    assert state.segments[0].end_ms == 1300
    assert state.segments[1].start_ms == 1300
    assert state.segments[0].tone_label == "clean"
    assert state.segments[1].tone_label == "metal"
    assert state.dirty is True


def test_move_boundary_out_of_range_left_index_rejected(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)

    result = state.move_boundary(0, 500)  # no segment 1 to pair with

    assert not result.ok
    assert state.dirty is False


def test_move_boundary_invalid_move_rejected_no_mutation(store, track_hash):
    store.save_segments(
        track_hash,
        [make_segment(track_hash, 0, 1000), make_segment(track_hash, 1000, 2000)],
    )
    state = _state(store)
    state.load_track(track_hash)

    result = state.move_boundary(0, 2000)  # would invert segment 1

    assert not result.ok
    assert state.segments[0].end_ms == 1000
    assert state.segments[1].start_ms == 1000
    assert state.dirty is False


# =========================================================================
# confirm / confirm_all
# =========================================================================

def test_confirm_stamps_confidence_and_corrected_keeps_label(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000, "overdrive", confidence=0.4)])
    state = _state(store)
    state.load_track(track_hash)

    result = state.confirm(0)

    assert result.ok
    assert state.segments[0].tone_label == "overdrive"
    assert state.segments[0].confidence == 1.0
    assert state.segments[0].manually_corrected is True
    assert state.dirty is True


def test_confirm_out_of_range_index_rejected(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)

    assert not state.confirm(5).ok


def test_confirm_all_stamps_every_segment_in_one_batch(store, track_hash):
    store.save_segments(
        track_hash,
        [make_segment(track_hash, 0, 1000, "clean"), make_segment(track_hash, 1000, 2000, "metal")],
    )
    state = _state(store)
    state.load_track(track_hash)
    events: list[StateEvent] = []
    state.subscribe(events.append)

    result = state.confirm_all()

    assert result.ok
    assert all(s.manually_corrected for s in state.segments)
    assert [e.kind for e in events] == ["segments", "dirty"]  # one batch, not per-segment


# =========================================================================
# merge_run
# =========================================================================

def test_merge_run_valid_run_collapses_segments_and_moves_selection(store, track_hash):
    store.save_segments(
        track_hash,
        [
            make_segment(track_hash, 0, 1000, "clean"),
            make_segment(track_hash, 1000, 2000, "metal"),
            make_segment(track_hash, 2000, 3000, "metal"),
        ],
    )
    state = _state(store)
    state.load_track(track_hash)

    result = state.merge_run(1)

    assert result.ok
    assert len(state.segments) == 2
    assert state.segments[1].start_ms == 1000
    assert state.segments[1].end_ms == 3000
    assert state.selection_index == 1
    assert state.dirty is True


def test_merge_run_isolated_segment_rejected_no_mutation(store, track_hash):
    store.save_segments(
        track_hash,
        [make_segment(track_hash, 0, 1000, "clean"), make_segment(track_hash, 1000, 2000, "metal")],
    )
    state = _state(store)
    state.load_track(track_hash)

    result = state.merge_run(0)

    assert not result.ok
    assert len(state.segments) == 2
    assert state.dirty is False


# =========================================================================
# set_excluded / calibration_copy_exists
# =========================================================================

def test_set_excluded_is_eager_not_batched_with_save(store, track_hash):
    state = _state(store)
    state.load_track(track_hash)
    events: list[StateEvent] = []
    state.subscribe(events.append)

    state.set_excluded(True)

    assert state.excluded is True
    assert store.get_calibration_excluded(track_hash) is True  # written without save()
    assert [e.kind for e in events] == ["excluded"]


def test_excluded_reflects_store_on_load(store, track_hash):
    store.set_calibration_excluded(track_hash, True)
    state = _state(store)
    state.load_track(track_hash)
    assert state.excluded is True


def test_calibration_copy_exists_false_before_any_save(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)
    assert state.calibration_copy_exists() is False


def test_calibration_copy_exists_true_after_save(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000, "clean")])
    state = _state(store)
    state.load_track(track_hash)
    state.relabel(0, "metal")
    state.save()
    assert state.calibration_copy_exists() is True


# =========================================================================
# save / discard
# =========================================================================

def test_save_noop_when_not_dirty(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)
    events: list[StateEvent] = []
    state.subscribe(events.append)

    state.save()

    assert events == []  # nothing to flush, no signal noise


def test_save_flushes_pending_updates_and_clears_dirty(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000, "clean")])
    state = _state(store)
    state.load_track(track_hash)
    state.relabel(0, "metal")
    events: list[StateEvent] = []
    state.subscribe(events.append)

    state.save()

    assert state.dirty is False
    assert store.get_segments(track_hash)[0].tone_label == "metal"
    assert [e.kind for e in events] == ["dirty", "saved"]


def test_save_preserves_original_in_calibration_copy_across_merge(store, track_hash):
    store.save_segments(
        track_hash,
        [
            make_segment(track_hash, 0, 1000, "clean"),
            make_segment(track_hash, 1000, 2000, "metal"),
            make_segment(track_hash, 2000, 3000, "metal"),
        ],
    )
    state = _state(store)
    state.load_track(track_hash)
    state.merge_run(1)
    state.save()

    live = store.get_segments(track_hash)
    assert len(live) == 2  # merged in the live table

    original = store.get_calibration_segments(track_hash)
    assert len(original) == 3  # pre-merge snapshot preserved
    assert [s.tone_label for s in original] == ["clean", "metal", "metal"]


def test_discard_reverts_to_last_saved_state(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000, "clean")])
    state = _state(store)
    state.load_track(track_hash)
    state.relabel(0, "metal")

    state.discard()

    assert state.dirty is False
    assert state.segments[0].tone_label == "clean"


def test_discard_emits_segments_not_loaded(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000)])
    state = _state(store)
    state.load_track(track_hash)
    state.confirm(0)
    events: list[StateEvent] = []
    state.subscribe(events.append)

    state.discard()

    assert [e.kind for e in events] == ["segments", "dirty"]


# =========================================================================
# loading a new track resets pending state
# =========================================================================

def test_load_track_clears_pending_edits_from_previous_track(store, db, track_hash):
    db.execute(
        "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at) VALUES (?,?,?,?)",
        ("hash_b", "b.wav", 10000, "2026-01-01T00:00:00+00:00"),
    )
    db.commit()
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000, "clean")])
    store.save_segments("hash_b", [make_segment("hash_b", 0, 1000, "clean")])
    state = _state(store)
    state.load_track(track_hash)
    state.relabel(0, "metal")
    assert state.dirty is True

    state.load_track("hash_b")

    assert state.dirty is False


def test_clear_resets_pending_edits(store, track_hash):
    store.save_segments(track_hash, [make_segment(track_hash, 0, 1000, "clean")])
    state = _state(store)
    state.load_track(track_hash)
    state.relabel(0, "metal")
    assert state.dirty is True

    state.clear()

    assert state.dirty is False
