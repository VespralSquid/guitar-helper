from __future__ import annotations

from abc import ABC, abstractmethod


class IMidiPort(ABC):

    @abstractmethod
    def send_program_change(self, channel: int, program: int) -> None:
        """Dispatch a MIDI Program Change (program is 0-indexed)."""

    @abstractmethod
    def close(self) -> None:
        """Release the underlying port."""
