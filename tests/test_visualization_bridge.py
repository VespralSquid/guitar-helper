from __future__ import annotations

import numpy as np

from guitar_helper.playback.visualization_bridge import VisualizationBridge


def test_get_nowait_empty_returns_none():
    bridge = VisualizationBridge()
    assert bridge.get_nowait() is None


def test_produce_then_consume():
    bridge = VisualizationBridge()
    chunk = np.zeros((4, 2), dtype=np.float32)
    bridge.queue.put_nowait((100, chunk))
    cursor, out = bridge.get_nowait()
    assert cursor == 100
    assert out is chunk
    assert bridge.get_nowait() is None


def test_bounded_does_not_grow_unbounded():
    bridge = VisualizationBridge(maxsize=2)
    bridge.queue.put_nowait((0, np.zeros(1)))
    bridge.queue.put_nowait((1, np.zeros(1)))
    assert bridge.queue.full()
