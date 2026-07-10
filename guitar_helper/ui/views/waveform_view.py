"""Static min/max-envelope waveform with a playhead and click-to-seek.

The waveform is downsampled once per load to a fixed column budget — never
raw frames — so render cost is constant regardless of track length.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal

from guitar_helper.playback.audio_buffer import AudioBuffer
from guitar_helper.ui import theme


class WaveformView(pg.PlotWidget):
    seekRequested = Signal(int)  # position_ms

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._duration_ms = 0

        self.setMenuEnabled(False)
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.getPlotItem().hideAxis("left")
        self.getPlotItem().showGrid(x=False, y=False)

        self._upper = pg.PlotDataItem(pen=theme.tone_pen("other"))
        self._lower = pg.PlotDataItem(pen=theme.tone_pen("other"))
        self._fill = pg.FillBetweenItem(
            self._upper, self._lower, brush=pg.mkBrush(theme.WAVEFORM_COLOR)
        )
        self.addItem(self._fill)
        self.addItem(self._upper)
        self.addItem(self._lower)

        self._playhead = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen(theme.PLAYHEAD_COLOR, width=theme.PLAYHEAD_WIDTH),
        )
        self.addItem(self._playhead)

        self.scene().sigMouseClicked.connect(self._on_scene_clicked)

    @property
    def plot_item(self) -> pg.PlotItem:
        return self.getPlotItem()

    def load(self, buffer: AudioBuffer) -> None:
        mono = buffer.data.mean(axis=1) if buffer.data.ndim == 2 else buffer.data
        self._duration_ms = buffer.duration_ms
        columns = min(theme.WAVEFORM_COLUMNS, mono.shape[0])

        if columns == 0:
            self._upper.setData([], [])
            self._lower.setData([], [])
            self._playhead.setValue(0)
            return

        chunks = np.array_split(mono, columns)
        maxs = np.fromiter((c.max() if c.size else 0.0 for c in chunks), dtype=np.float32, count=columns)
        mins = np.fromiter((c.min() if c.size else 0.0 for c in chunks), dtype=np.float32, count=columns)
        ms_per_col = self._duration_ms / columns
        xs = (np.arange(columns) + 0.5) * ms_per_col

        self._upper.setData(xs, maxs)
        self._lower.setData(xs, mins)
        self.setXRange(0, self._duration_ms, padding=0)
        self._playhead.setValue(0)

    def set_playhead_ms(self, position_ms: int) -> None:
        self._playhead.setValue(position_ms)

    def _on_scene_clicked(self, event) -> None:
        if self._duration_ms <= 0:
            return
        view_pos = self.getPlotItem().getViewBox().mapSceneToView(event.scenePos())
        ms = int(max(0, min(view_pos.x(), self._duration_ms)))
        self.seekRequested.emit(ms)
