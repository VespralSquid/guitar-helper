from __future__ import annotations

import random

from guitar_helper.db.interfaces import Track
from guitar_helper.ui.state.queue_state import QueueEvent, QueueState


def make_track(h: str) -> Track:
    return Track(
        file_hash=h,
        filename=f"{h}.wav",
        title=h.upper(),
        artist="Artist",
        duration_ms=1000,
        source_path=f"C:/music/{h}.wav",
        calibration_excluded=False,
        corrected_count=0,
        total_count=0,
        analysed_at="2026-01-01T00:00:00+00:00",
    )


def make_queue(n: int = 4, seed: int = 7) -> tuple[QueueState, list[Track]]:
    tracks = [make_track(f"t{i}") for i in range(n)]
    state = QueueState(rng=random.Random(seed))
    state.set_queue(tracks)
    return state, tracks


def test_set_queue_sets_current():
    state, tracks = make_queue()
    assert state.current_index == 0
    assert state.current_track == tracks[0]
    assert state.tracks == tracks


def test_set_queue_with_start_index():
    state, tracks = make_queue()
    state.set_queue(tracks, start_index=2)
    assert state.current_track == tracks[2]


def test_advance_and_exhaustion_repeat_off():
    state, tracks = make_queue(2)
    assert state.advance() == tracks[1]
    assert state.advance() is None
    assert state.current_track == tracks[1]  # stays on the last track


def test_repeat_all_wraps():
    state, tracks = make_queue(2)
    state.cycle_repeat()  # off -> all
    assert state.repeat == "all"
    state.advance()
    assert state.advance() == tracks[0]


def test_repeat_one_auto_repeats_manual_moves_on():
    state, tracks = make_queue(3)
    state.cycle_repeat()  # all
    state.cycle_repeat()  # one
    assert state.repeat == "one"
    assert state.advance(manual=False) == tracks[0]
    assert state.advance(manual=True) == tracks[1]


def test_previous_at_start_stays():
    state, tracks = make_queue()
    assert state.previous() == tracks[0]
    state.advance(manual=True)
    assert state.previous() == tracks[0]


def test_play_at():
    state, tracks = make_queue()
    assert state.play_at(2) == tracks[2]
    assert state.play_at(99) is None


def test_move_keeps_current_track():
    state, tracks = make_queue()
    state.play_at(1)
    state.move(1, 3)
    assert state.current_track == tracks[1]
    assert state.tracks[3] == tracks[1]


def test_remove_before_current_adjusts_index():
    state, tracks = make_queue()
    state.play_at(2)
    state.remove(0)
    assert state.current_track == tracks[2]


def test_remove_current_moves_to_next():
    state, tracks = make_queue(3)
    state.play_at(1)
    state.remove(1)
    assert state.current_track == tracks[2]


def test_remove_last_remaining_clears_current():
    state, _ = make_queue(1)
    state.remove(0)
    assert state.current_index is None
    assert state.tracks == []


def test_play_next_moves_after_current():
    state, tracks = make_queue(4)
    state.play_next(3)
    assert state.tracks[1] == tracks[3]
    assert state.current_track == tracks[0]


def test_shuffle_is_seed_deterministic_and_current_first():
    state_a, tracks = make_queue(6, seed=42)
    state_b, _ = make_queue(6, seed=42)
    state_a.play_at(2)
    state_b.play_at(2)
    state_a.set_shuffle(True)
    state_b.set_shuffle(True)
    assert state_a.tracks == state_b.tracks
    assert state_a.tracks[0] == tracks[2]
    assert state_a.current_index == 0


def test_unshuffle_restores_original_order():
    state, tracks = make_queue(6)
    state.play_at(2)
    state.set_shuffle(True)
    state.set_shuffle(False)
    assert state.tracks == tracks
    assert state.current_track == tracks[2]


def test_unshuffle_respects_removals_made_while_shuffled():
    state, tracks = make_queue(4)
    state.set_shuffle(True)
    removed = state.tracks[3]
    state.remove(3)
    state.set_shuffle(False)
    assert removed not in state.tracks
    assert state.tracks == [t for t in tracks if t != removed]


def test_clear():
    state, _ = make_queue()
    state.clear()
    assert state.tracks == []
    assert state.current_index is None
    assert state.advance() is None


def test_events_emitted():
    state, tracks = make_queue()
    kinds: list[str] = []
    state.subscribe(lambda e: kinds.append(e.kind))
    state.advance(manual=True)
    assert kinds == ["current"]
    kinds.clear()
    state.set_queue(tracks)
    assert kinds == ["queue", "current"]
    kinds.clear()
    state.move(0, 1)
    assert kinds == ["queue"]


def test_event_dataclass_kind():
    assert QueueEvent("queue").kind == "queue"
