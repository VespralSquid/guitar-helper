from __future__ import annotations

import mido

from guitar_helper.midi.interfaces import IMidiPort

DEFAULT_PORT_NAME = "loopMIDI Port 1"


class MidiPortNotFoundError(RuntimeError):
    """Raised when the configured MIDI output port is not available."""


class MidoPort(IMidiPort):
    """Live IMidiPort backed by mido + rtmidi. Sends PC into a loopMIDI port.

    Fails fast at construction if the named port is absent so a missing
    loopMIDI/Ableton setup is reported before playback starts, not silently
    swallowed mid-song.
    """

    def __init__(self, port_name: str = DEFAULT_PORT_NAME) -> None:
        available = mido.get_output_names()
        if port_name not in available:
            raise MidiPortNotFoundError(
                f"MIDI output port {port_name!r} not found. "
                f"Is loopMIDI running with that port? Available: {available}"
            )
        self._port = mido.open_output(port_name)
        self.port_name = port_name

    def send_program_change(self, channel: int, program: int) -> None:
        self._port.send(mido.Message("program_change", channel=channel, program=program))

    def close(self) -> None:
        if not self._port.closed:
            self._port.close()
