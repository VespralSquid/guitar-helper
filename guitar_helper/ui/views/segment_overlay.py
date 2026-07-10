"""Tone-band regions drawn over the waveform plot.

M3 scope: fixed (non-draggable) bands + selection highlight only. Draggable
boundary handles are added in M4.
"""
from __future__ import annotations

import pyqtgraph as pg

from guitar_helper.db.interfaces import Segment
from guitar_helper.ui import theme


class SegmentOverlay:
    def __init__(self, plot_item: pg.PlotItem) -> None:
        self._plot_item = plot_item
        self._segments: list[Segment] = []
        self._regions: list[pg.LinearRegionItem] = []

    def set_segments(self, segments: list[Segment]) -> None:
        for region in self._regions:
            self._plot_item.removeItem(region)
        self._regions = []
        self._segments = list(segments)

        for segment in self._segments:
            region = pg.LinearRegionItem(
                values=(segment.start_ms, segment.end_ms),
                movable=False,
                brush=theme.tone_brush(segment.tone_label),
                pen=theme.tone_pen(segment.tone_label),
            )
            region.setZValue(-10)
            self._plot_item.addItem(region)
            self._regions.append(region)

    def set_selected(self, index: int | None) -> None:
        for i, (region, segment) in enumerate(zip(self._regions, self._segments)):
            region.setBrush(theme.tone_brush(segment.tone_label, selected=(i == index)))
            region.update()
