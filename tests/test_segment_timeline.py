from __future__ import annotations

import pytest

from guitar_helper.ui.views.segment_timeline import ms_to_x, x_to_ms


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
# Qt interaction (headless)
# ------------------------------------------------------------------

pytestqt = pytest.importorskip("pytestqt")


def _make_timeline(qtbot, duration_ms=10_000):
    from PySide6.QtCore import Qt

    from guitar_helper.ui.views.segment_timeline import SegmentTimeline
    from tests.conftest import make_segment

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
    with qtbot.waitSignal(timeline.seekRequested, timeout=1000) as blocker:
        qtbot.mouseClick(
            timeline, qt.MouseButton.LeftButton, pos=QPoint(400, 10)
        )
    assert abs(blocker.args[0] - 5_000) <= 20  # ~1px tolerance


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
