from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Summary:
    count: int
    mean: float
    minimum: float
    maximum: float
    p95: float

    @classmethod
    def of(cls, values: list[float]) -> "Summary | None":
        if not values:
            return None
        ordered = sorted(values)
        k = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
        return cls(
            count=len(ordered),
            mean=sum(ordered) / len(ordered),
            minimum=ordered[0],
            maximum=ordered[-1],
            p95=ordered[k],
        )


class Samples:
    """Thread-safe collector of float millisecond samples.

    Used off the audio callback (dispatcher thread, sampler thread). The audio
    callback never touches this — it writes a single float attribute instead.
    """

    def __init__(self) -> None:
        self._values: list[float] = []
        self._lock = threading.Lock()

    def add(self, ms: float) -> None:
        with self._lock:
            self._values.append(ms)

    def summary(self) -> Summary | None:
        with self._lock:
            return Summary.of(list(self._values))


@dataclass
class DispatchProbe:
    """Optional instrumentation handed to MidiDispatcher during measurement.

    None in production, so the live dispatch path pays nothing. When present,
    the dispatcher records the wall interval between poll ticks (jitter) and the
    time each ``send_program_change`` call takes to return (in-app send cost).
    """

    poll_ms: Samples = field(default_factory=Samples)
    send_ms: Samples = field(default_factory=Samples)
