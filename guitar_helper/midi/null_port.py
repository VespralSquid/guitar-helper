from __future__ import annotations

from guitar_helper.midi.interfaces import IMidiPort


class NullMidiPort(IMidiPort):
    """Inert IMidiPort used when no MIDI output is available.

    Distinct from MockMidiPort, which records every dispatch: a live session
    with no loopMIDI would grow that list for the whole run with nobody
    reading it. Program Changes are dropped; the app stays usable for
    reviewing and correcting segments.
    """

    def send_program_change(self, channel: int, program: int) -> None:
        pass

    def close(self) -> None:
        pass
