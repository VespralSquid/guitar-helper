from __future__ import annotations

from guitar_helper.midi.interfaces import IMidiPort


class MockMidiPort(IMidiPort):
    """In-memory IMidiPort for tests — records every dispatch, opens no hardware."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, int]] = []
        self.closed = False

    def send_program_change(self, channel: int, program: int) -> None:
        self.sent.append((channel, program))

    def close(self) -> None:
        self.closed = True
