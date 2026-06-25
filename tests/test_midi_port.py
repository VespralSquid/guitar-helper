from __future__ import annotations

import pytest

from guitar_helper.midi.mock_port import MockMidiPort


def test_mock_records_sends():
    port = MockMidiPort()
    port.send_program_change(0, 4)
    port.send_program_change(2, 1)
    assert port.sent == [(0, 4), (2, 1)]
    assert not port.closed


def test_mock_close():
    port = MockMidiPort()
    port.close()
    assert port.closed


def test_mido_port_fails_fast_on_missing_port():
    pytest.importorskip("rtmidi")  # runtime-only backend; CI skips
    from guitar_helper.midi.mido_port import MidiPortNotFoundError, MidoPort

    with pytest.raises(MidiPortNotFoundError):
        MidoPort("no such port 9xZ")
