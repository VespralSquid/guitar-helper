from __future__ import annotations

import pytest

from guitar_helper.ui.views.segment_timeline import boundary_at_x, ms_to_x, x_to_ms
from tests.conftest import make_segment


def test_ms_to_x_maps_endpoints():
    assert ms_to_x(0, 10_000, 800) == 0
    assert ms_to_x(10_000, 10_000, 800) == 800
    assert ms_to_x(5_000, 10_000, 800) == 400


def test_ms_to_x_clamps_and_handles_zero_duration():
    assert ms_to_x(-50, 10_000, 800) == 0
    assert ms_to_x(20_000, 10_000, 800) == 800
    assert ms_to_x(5_000, 0, 800) == 0


def test_x_to_ms_maps_and_clamps():
    assert x_to_ms(0, 10_000, 800) == 0
    assert x_to_ms(800, 10_000, 800) == 10_000
    assert x_to_ms(-10, 10_000, 800) == 0
    assert x_to_ms(900, 10_000, 800) == 10_000
    assert x_to_ms(400, 10_000, 0) == 0


def test_roundtrip_within_one_pixel():
    duration, width = 187_000, 640
    for ms in (0, 1, 12_345, 93_500, 186_999, 187_000):
        x = ms_to_x(ms, duration, width)
        assert abs(x_to_ms(x, duration, width) - ms) <= duration / width + 1


# ------------------------------------------------------------------
# boundary_at_x (pure hit-testing)
# ------------------------------------------------------------------

def _segs(*bounds):
    return [
        make_segment("h", start, end, "clean") for start, end in zip(bounds, bounds[1:])
    ]


def test_boundary_at_x_hit_within_tolerance():
    segments = _segs(0, 5_000, 10_000)  # boundary at ms=5000 -> x=400 (duration 10000, width 800)
    assert boundary_at_x(400, segments, 10_000, 800, hit_px=6) == 0
    assert boundary_at_x(404, segments, 10_000, 800, hit_px=6) == 0
    assert boundary_at_x(396, segments, 10_000, 800, hit_px=6) == 0


def test_boundary_at_x_miss_outside_tolerance():
    segments = _segs(0, 5_000, 10_000)
    assert boundary_at_x(200, segments, 10_000, 800, hit_px=6) is None
    assert boundary_at_x(420, segments, 10_000, 800, hit_px=6) is None


def test_boundary_at_x_picks_correct_index_among_several():
    segments = _segs(0, 2_500, 5_000, 7_500, 10_000)  # boundaries at x=200,400,600
    assert boundary_at_x(200, segments, 10_000, 800, hit_px=6) == 0
    assert boundary_at_x(400, segments, 10_000, 800, hit_px=6) == 1
    assert boundary_at_x(600, segments, 10_000, 800, hit_px=6) == 2


def test_boundary_at_x_no_boundaries_for_single_or_no_segment():
    assert boundary_at_x(400, _segs(0, 10_000), 10_000, 800, hit_px=6) is None
    assert boundary_at_x(400, [], 10_000, 800, hit_px=6) is None


# ------------------------------------------------------------------
# Qt interaction (headless)
# ------------------------------------------------------------------

pytestqt = pytest.importorskip("pytestqt")


def _make_timeline(qtbot, duration_ms=10_000):
    from PySide6.QtCore import Qt

    from guitar_helper.ui.views.segment_timeline import SegmentTimeline

    timeline = SegmentTimeline()
    timeline.resize(800, timeline.height())
    qtbot.addWidget(timeline)
    timeline.set_duration_ms(duration_ms)
    timeline.set_segments(
        [
            make_segment("h", 0, 5_000, "clean"),
            make_segment("h", 5_000, 10_000, "metal"),
        ]
    )
    return timeline, Qt


def test_click_emits_seek_at_mapped_position(qtbot):
    from PySide6.QtCore import QPoint

    timeline, qt = _make_timeline(qtbot)
    # x=200 (ms~2500) is well clear of the boundary at x=400 (ms=5000) +/- BOUNDARY_HIT_PX
    with qtbot.waitSignal(timeline.seekRequested, timeout=1000) as blocker:
        qtbot.mouseClick(
            timeline, qt.MouseButton.LeftButton, pos=QPoint(200, 10)
        )
    assert abs(blocker.args[0] - 2_500) <= 20  # ~1px tolerance


def test_playhead_repaints_only_on_pixel_change(qtbot):
    timeline, _ = _make_timeline(qtbot, duration_ms=800_000)  # 1px = 1000ms
    timeline.set_playhead_ms(1_000)
    x_before = timeline._last_playhead_x
    timeline.set_playhead_ms(1_400)  # same pixel column
    assert timeline._last_playhead_x == x_before
    timeline.set_playhead_ms(2_100)  # crosses a pixel
    assert timeline._last_playhead_x != x_before


def test_selection_and_paint_smoke(qtbot):
    timeline, _ = _make_timeline(qtbot)
    timeline.set_selected(1)
    timeline.show()
    qtbot.waitExposed(timeline)  # forces a real paintEvent pass
    timeline.set_selected(None)


# ------------------------------------------------------------------
# Boundary dragging
# ------------------------------------------------------------------

def test_drag_boundary_emits_boundary_edit_requested(qtbot):
    from PySide6.QtCore import QPoint

    timeline, qt = _make_timeline(qtbot)  # boundary at x=400 (ms=5000)
    with qtbot.waitSignal(timeline.boundaryEditRequested, timeout=1000) as blocker:
        qtbot.mousePress(timeline, qt.MouseButton.LeftButton, pos=QPoint(400, 10))
        qtbot.mouseMove(timeline, pos=QPoint(450, 10))
        qtbot.mouseRelease(timeline, qt.MouseButton.LeftButton, pos=QPoint(450, 10))

    left_index, new_ms = blocker.args
    assert left_index == 0
    assert abs(new_ms - 5_625) <= 20  # 450/800 * 10000


def test_drag_does_not_emit_seek(qtbot):
    from PySide6.QtCore import QPoint

    timeline, qt = _make_timeline(qtbot)
    with qtbot.assertNotEmitted(timeline.seekRequested, wait=200):
        qtbot.mousePress(timeline, qt.MouseButton.LeftButton, pos=QPoint(400, 10))
        qtbot.mouseMove(timeline, pos=QPoint(450, 10))
        qtbot.mouseRelease(timeline, qt.MouseButton.LeftButton, pos=QPoint(450, 10))


def test_drag_clamps_to_valid_range(qtbot):
    from PySide6.QtCore import QPoint

    timeline, qt = _make_timeline(qtbot)  # segments: 0-5000, 5000-10000
    with qtbot.waitSignal(timeline.boundaryEditRequested, timeout=1000) as blocker:
        qtbot.mousePress(timeline, qt.MouseButton.LeftButton, pos=QPoint(400, 10))
        qtbot.mouseMove(timeline, pos=QPoint(0, 10))  # drag far past the left segment's start
        qtbot.mouseRelease(timeline, qt.MouseButton.LeftButton, pos=QPoint(0, 10))

    left_index, new_ms = blocker.args
    assert left_index == 0
    assert new_ms > 0  # clamped above segments[0].start_ms=0, never inverts it
