from __future__ import annotations

import threading

from guitar_helper.playback.dispatch_log import (
    GAP,
    HOLD,
    SEND,
    DispatchEvent,
    DispatchLogBuffer,
)


def _send(position_ms: int, pc: int, tone: str = "metal") -> DispatchEvent:
    return DispatchEvent(position_ms=position_ms, kind=SEND, tone=tone, pc=pc)


def test_drain_returns_and_empties():
    log = DispatchLogBuffer()
    log.record(_send(1000, 4))
    log.record(DispatchEvent(position_ms=2000, kind=HOLD, tone="other"))

    assert [e.position_ms for e in log.drain()] == [1000, 2000]
    assert log.drain() == []


def test_ring_buffer_drops_oldest_beyond_maxlen():
    log = DispatchLogBuffer(maxlen=3)
    for ms in (100, 200, 300, 400):
        log.record(_send(ms, 0))

    assert [e.position_ms for e in log.drain()] == [200, 300, 400]


def test_last_send_survives_drain():
    """The active-preset indicator must be right for a viewer who opens Output
    after the switch already happened — a drain must not erase it."""
    log = DispatchLogBuffer()
    log.record(_send(1000, 4))
    log.drain()

    assert log.last_send is not None
    assert log.last_send.pc == 4


def test_last_send_ignores_non_send_events():
    log = DispatchLogBuffer()
    log.record(_send(1000, 4))
    log.record(DispatchEvent(position_ms=2000, kind=HOLD, tone="other"))
    log.record(DispatchEvent(position_ms=3000, kind=GAP, tone=None))

    assert log.last_send.position_ms == 1000


def test_clear_resets_events_and_last_send():
    log = DispatchLogBuffer()
    log.record(_send(1000, 4))
    log.clear()

    assert log.drain() == []
    assert log.last_send is None


def test_concurrent_records_are_all_retained():
    """The dispatcher thread writes while the Qt thread drains — no sample lost,
    no exception raised."""
    log = DispatchLogBuffer(maxlen=1000)

    def write(offset: int) -> None:
        for i in range(100):
            log.record(_send(offset + i, 0))

    threads = [threading.Thread(target=write, args=(o,)) for o in (0, 1000, 2000)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(log.drain()) == 300
