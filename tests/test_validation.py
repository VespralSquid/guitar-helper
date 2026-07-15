from __future__ import annotations

from guitar_helper.analysis.tone_classifier import TONE_LABELS
from guitar_helper.db.interfaces import Segment
from guitar_helper.ui.editor.validation import (
    apply_boundary,
    apply_boundary_move,
    apply_confirm,
    apply_relabel,
    validate_boundary,
    validate_boundary_move,
    validate_relabel,
)


def _seg(seg_id: int, start_ms: int, end_ms: int, label: str = "edge") -> Segment:
    return Segment(
        id=seg_id, file_hash="h",
        start_ms=start_ms, end_ms=end_ms,
        tone_label=label, confidence=0.3, manually_corrected=False,
    )


# =========================================================================
# validate_relabel
# =========================================================================

def test_validate_relabel_known_label_ok():
    result = validate_relabel("metal", TONE_LABELS)
    assert result.ok
    assert result.error is None


def test_validate_relabel_unknown_label_rejected():
    result = validate_relabel("fuzz", TONE_LABELS)
    assert not result.ok
    assert "fuzz" in result.error
    assert "clean" in result.error  # valid list is echoed back


# =========================================================================
# validate_boundary
# =========================================================================

def test_validate_boundary_valid_edit_ok():
    segments = [_seg(1, 0, 1000), _seg(2, 1000, 2000), _seg(3, 2000, 3000)]
    result = validate_boundary(segments, 1, 1000, 1900)
    assert result.ok


def test_validate_boundary_start_not_before_end_rejected():
    segments = [_seg(1, 0, 1000)]
    result = validate_boundary(segments, 0, 500, 500)
    assert not result.ok
    assert "start_ms must be less than end_ms" in result.error


def test_validate_boundary_overlaps_previous_rejected():
    segments = [_seg(1, 0, 1000), _seg(2, 1000, 2000)]
    result = validate_boundary(segments, 1, 900, 1900)
    assert not result.ok
    assert "overlaps segment 0" in result.error


def test_validate_boundary_overlaps_next_rejected():
    segments = [_seg(1, 0, 1000), _seg(2, 1000, 2000)]
    result = validate_boundary(segments, 0, 0, 1100)
    assert not result.ok
    assert "overlaps segment 1" in result.error


def test_validate_boundary_first_segment_has_no_prev_check():
    segments = [_seg(1, 0, 1000), _seg(2, 1000, 2000)]
    result = validate_boundary(segments, 0, -500, 900)
    assert result.ok  # no segment -1 to overlap


def test_validate_boundary_last_segment_has_no_next_check():
    segments = [_seg(1, 0, 1000), _seg(2, 1000, 2000)]
    result = validate_boundary(segments, 1, 1100, 5000)
    assert result.ok  # no segment 2 to overlap


# =========================================================================
# validate_boundary_move / apply_boundary_move
# =========================================================================

def test_validate_boundary_move_valid_ok():
    segments = [_seg(1, 0, 1000), _seg(2, 1000, 2000)]
    result = validate_boundary_move(segments, 0, 1500)
    assert result.ok


def test_validate_boundary_move_rejects_at_or_before_left_start():
    segments = [_seg(1, 0, 1000), _seg(2, 1000, 2000)]
    assert not validate_boundary_move(segments, 0, 0).ok
    assert not validate_boundary_move(segments, 0, -100).ok


def test_validate_boundary_move_rejects_at_or_after_right_end():
    segments = [_seg(1, 0, 1000), _seg(2, 1000, 2000)]
    assert not validate_boundary_move(segments, 0, 2000).ok
    assert not validate_boundary_move(segments, 0, 2500).ok


def test_apply_boundary_move_updates_both_sides_contiguously():
    left = _seg(1, 0, 1000, label="clean")
    right = _seg(2, 1000, 2000, label="metal")
    new_left, new_right = apply_boundary_move(left, right, 1300)
    assert new_left.end_ms == 1300
    assert new_right.start_ms == 1300
    assert new_left.start_ms == 0  # unaffected
    assert new_right.end_ms == 2000  # unaffected
    assert new_left.tone_label == "clean"  # label untouched by a boundary move
    assert new_right.tone_label == "metal"
    assert new_left.confidence == 1.0 and new_left.manually_corrected is True
    assert new_right.confidence == 1.0 and new_right.manually_corrected is True


# =========================================================================
# apply_relabel / apply_boundary / apply_confirm
# =========================================================================

def test_apply_relabel_sets_label_confidence_and_corrected():
    seg = _seg(1, 0, 1000, label="edge")
    updated = apply_relabel(seg, "metal")
    assert updated.id == seg.id
    assert updated.file_hash == seg.file_hash
    assert updated.start_ms == seg.start_ms
    assert updated.end_ms == seg.end_ms
    assert updated.tone_label == "metal"
    assert updated.confidence == 1.0
    assert updated.manually_corrected is True


def test_apply_boundary_sets_bounds_label_confidence_and_corrected():
    seg = _seg(1, 0, 1000, label="edge")
    updated = apply_boundary(seg, 100, 900, "crunch")
    assert updated.id == seg.id
    assert updated.file_hash == seg.file_hash
    assert updated.start_ms == 100
    assert updated.end_ms == 900
    assert updated.tone_label == "crunch"
    assert updated.confidence == 1.0
    assert updated.manually_corrected is True


def test_apply_confirm_keeps_label_stamps_confidence_and_corrected():
    seg = _seg(1, 0, 1000, label="overdrive")
    updated = apply_confirm(seg)
    assert updated.id == seg.id
    assert updated.start_ms == seg.start_ms
    assert updated.end_ms == seg.end_ms
    assert updated.tone_label == "overdrive"  # unchanged
    assert updated.confidence == 1.0
    assert updated.manually_corrected is True
