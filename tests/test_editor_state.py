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
