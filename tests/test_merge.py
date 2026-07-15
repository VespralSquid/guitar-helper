from __future__ import annotations

from guitar_helper.db.interfaces import Segment
from guitar_helper.ui.editor.merge import find_same_tone_run, merge_run


def _seg(seg_id: int, start_ms: int, end_ms: int, label: str) -> Segment:
    return Segment(
        id=seg_id, file_hash="h",
        start_ms=start_ms, end_ms=end_ms,
        tone_label=label, confidence=0.4, manually_corrected=False,
    )


def _run(*labels: str) -> list[Segment]:
    return [_seg(i, i * 1000, (i + 1) * 1000, label) for i, label in enumerate(labels)]


# =========================================================================
# find_same_tone_run
# =========================================================================

def test_find_run_isolated_segment_returns_self():
    segments = _run("clean", "metal", "clean")
    assert find_same_tone_run(segments, 1) == (1, 1)


def test_find_run_middle_of_run():
    segments = _run("clean", "metal", "metal", "metal", "clean")
    assert find_same_tone_run(segments, 2) == (1, 3)


def test_find_run_at_track_start():
    segments = _run("crunch", "crunch", "clean")
    assert find_same_tone_run(segments, 0) == (0, 1)


def test_find_run_at_track_end():
    segments = _run("clean", "crunch", "crunch")
    assert find_same_tone_run(segments, 2) == (1, 2)


def test_find_run_whole_track_single_run():
    segments = _run("metal", "metal", "metal")
    assert find_same_tone_run(segments, 1) == (0, 2)


def test_find_run_does_not_join_across_different_tone():
    segments = _run("crunch", "clean", "crunch")
    assert find_same_tone_run(segments, 0) == (0, 0)
    assert find_same_tone_run(segments, 2) == (2, 2)


# =========================================================================
# merge_run
# =========================================================================

def test_merge_run_spans_full_range_keeps_first_id_and_label():
    segments = _run("clean", "metal", "metal", "metal", "clean")
    plan = merge_run(segments, 1, 3)
    assert plan.keep.id == segments[1].id
    assert plan.keep.tone_label == "metal"
    assert plan.keep.start_ms == segments[1].start_ms
    assert plan.keep.end_ms == segments[3].end_ms
    assert plan.keep.confidence == 1.0
    assert plan.keep.manually_corrected is True


def test_merge_run_delete_ids_are_absorbed_segments_in_order():
    segments = _run("clean", "metal", "metal", "metal", "clean")
    plan = merge_run(segments, 1, 3)
    assert plan.delete_ids == [segments[2].id, segments[3].id]


def test_merge_run_single_segment_is_noop_plan():
    segments = _run("clean", "metal", "clean")
    plan = merge_run(segments, 1, 1)
    assert plan.delete_ids == []
    assert plan.keep.start_ms == segments[1].start_ms
    assert plan.keep.end_ms == segments[1].end_ms
    assert plan.keep.tone_label == "metal"


def test_merge_run_does_not_touch_boundaries_outside_run():
    segments = _run("clean", "metal", "metal", "crunch")
    plan = merge_run(segments, 1, 2)
    assert plan.keep.start_ms == segments[1].start_ms  # unchanged, not 0
    assert plan.keep.end_ms == segments[2].end_ms       # not segments[3]'s bounds
