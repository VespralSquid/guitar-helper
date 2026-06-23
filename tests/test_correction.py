from __future__ import annotations

from guitar_helper.correction.cli import _parse_command
from guitar_helper.db.interfaces import Segment


def _seg(seg_id: int, label: str = "edge", corrected: bool = False) -> Segment:
    return Segment(
        id=seg_id, file_hash="h",
        start_ms=seg_id * 1000, end_ms=(seg_id + 1) * 1000,
        tone_label=label, confidence=0.3, manually_corrected=corrected,
    )


def test_confirm_single_sets_flag_keeps_label():
    segs = [_seg(1, "edge"), _seg(2, "crunch")]
    result = _parse_command(["confirm", "0"], segs)
    assert result is not None
    assert len(result) == 1
    assert result[0].id == 1
    assert result[0].tone_label == "edge"  # label unchanged
    assert result[0].manually_corrected is True


def test_confirm_all_flags_every_segment():
    segs = [_seg(1, "edge"), _seg(2, "crunch")]
    result = _parse_command(["confirm", "all"], segs)
    assert len(result) == 2
    assert all(r.manually_corrected for r in result)
    assert [r.tone_label for r in result] == ["edge", "crunch"]


def test_confirm_out_of_range_returns_none():
    assert _parse_command(["confirm", "9"], [_seg(1)]) is None


def test_confirm_missing_arg_returns_none():
    assert _parse_command(["confirm"], [_seg(1)]) is None


def test_label_edit_still_flags_corrected():
    segs = [_seg(1, "edge")]
    result = _parse_command(["0", "crunch"], segs)
    assert result is not None
    assert result[0].tone_label == "crunch"
    assert result[0].manually_corrected is True
