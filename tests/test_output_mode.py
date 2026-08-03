from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402

from guitar_helper.playback.dispatch_log import (  # noqa: E402
    GAP,
    HOLD,
    MAX_EVENTS,
    SEND,
    UNMAPPED,
    DispatchEvent,
)
from guitar_helper.ui.modes.output import OutputMode, format_event, format_position  # noqa: E402

_PC_COL = 2
_NAME_COL = 1


def _make_output(qtbot, store) -> OutputMode:
    output = OutputMode(store)
    qtbot.addWidget(output)
    return output


def _row_for(output: OutputMode, tone_label: str) -> int:
    return next(
        i for i, p in enumerate(output.preset_model.presets) if p.tone_label == tone_label
    )


def _edit(output: OutputMode, tone_label: str, column: int, value) -> None:
    index = output.preset_model.index(_row_for(output, tone_label), column)
    output.preset_model.setData(index, value, Qt.ItemDataRole.EditRole)


def _pc_of(store, tone_label: str) -> int:
    return next(p.pc_number for p in store.get_presets() if p.tone_label == tone_label)


# ----------------------------------------------------------------------
# preset table
# ----------------------------------------------------------------------

def test_preset_table_lists_seeded_presets(qtbot, store):
    output = _make_output(qtbot, store)
    tones = {p.tone_label for p in output.preset_model.presets}
    assert {"clean", "edge", "overdrive", "crunch", "metal", "other"} == tones


def test_pc_edit_writes_through_to_the_store(qtbot, store):
    output = _make_output(qtbot, store)
    _edit(output, "clean", _PC_COL, "7")
    assert _pc_of(store, "clean") == 7


def test_pc_edit_signals_the_shell_to_refresh_the_dispatcher(qtbot, store):
    output = _make_output(qtbot, store)
    with qtbot.waitSignal(output.presetsChanged, timeout=1000):
        _edit(output, "clean", _PC_COL, "7")


def test_duplicate_pc_is_rejected_and_not_persisted(qtbot, store):
    output = _make_output(qtbot, store)
    with qtbot.waitSignal(output.statusMessage, timeout=1000) as blocker:
        _edit(output, "clean", _PC_COL, "4")  # metal's PC

    assert "metal" in blocker.args[0]
    assert _pc_of(store, "clean") == 0


def test_out_of_range_pc_is_rejected(qtbot, store):
    output = _make_output(qtbot, store)
    with qtbot.waitSignal(output.statusMessage, timeout=1000):
        _edit(output, "clean", _PC_COL, "200")
    assert _pc_of(store, "clean") == 0


def test_other_cannot_be_given_a_dispatchable_pc(qtbot, store):
    """The 'other' hold is a project invariant; the panel must not be a way
    around it."""
    output = _make_output(qtbot, store)
    with qtbot.waitSignal(output.statusMessage, timeout=1000):
        _edit(output, "other", _PC_COL, "9")
    assert _pc_of(store, "other") == -1


def test_non_numeric_pc_is_rejected_without_touching_the_store(qtbot, store):
    output = _make_output(qtbot, store)
    with qtbot.waitSignal(output.statusMessage, timeout=1000) as blocker:
        _edit(output, "clean", _PC_COL, "eleven")

    assert "number" in blocker.args[0].lower()
    assert _pc_of(store, "clean") == 0


def test_pc_display_keeps_the_no_dispatch_hint_editable(qtbot, store):
    """The 'other' row displays '-1 (no dispatch)'; re-committing that string
    must parse back to -1 rather than being read as garbage."""
    output = _make_output(qtbot, store)
    _edit(output, "other", _PC_COL, "-1 (no dispatch)")
    assert _pc_of(store, "other") == -1


def test_name_edit_persists(qtbot, store):
    output = _make_output(qtbot, store)
    _edit(output, "metal", _NAME_COL, "Nolly Rhythm")
    assert next(p.preset_name for p in store.get_presets() if p.tone_label == "metal") \
        == "Nolly Rhythm"


def test_blank_name_is_rejected(qtbot, store):
    output = _make_output(qtbot, store)
    with qtbot.waitSignal(output.statusMessage, timeout=1000):
        _edit(output, "metal", _NAME_COL, "   ")
    assert next(p.preset_name for p in store.get_presets() if p.tone_label == "metal") == "Metal"


# ----------------------------------------------------------------------
# test send
# ----------------------------------------------------------------------

def test_test_send_disabled_without_a_selection(qtbot, store):
    output = _make_output(qtbot, store)
    assert not output.test_send_button.isEnabled()


def test_test_send_emits_the_selected_rows_pc(qtbot, store):
    output = _make_output(qtbot, store)
    output.preset_table.selectRow(_row_for(output, "metal"))

    with qtbot.waitSignal(output.testSendRequested, timeout=1000) as blocker:
        output.test_send_button.click()

    assert blocker.args == [4]


def test_test_send_disabled_for_the_no_dispatch_row(qtbot, store):
    output = _make_output(qtbot, store)
    output.preset_table.selectRow(_row_for(output, "other"))
    assert not output.test_send_button.isEnabled()


# ----------------------------------------------------------------------
# dispatch offset
# ----------------------------------------------------------------------

def test_seeding_the_offset_does_not_emit(qtbot, store):
    """Loading the persisted value must not look like a user edit — otherwise
    startup rewrites the setting every run."""
    output = _make_output(qtbot, store)
    with qtbot.assertNotEmitted(output.dispatchOffsetChanged):
        output.set_dispatch_offset_ms(30)


def test_apply_emits_the_new_offset(qtbot, store):
    output = _make_output(qtbot, store)
    output.offset_spin.setValue(20)

    with qtbot.waitSignal(output.dispatchOffsetChanged, timeout=1000) as blocker:
        output.apply_offset_button.click()

    assert blocker.args == [20]


def test_apply_without_a_change_emits_nothing(qtbot, store):
    output = _make_output(qtbot, store)
    output.set_dispatch_offset_ms(20)
    with qtbot.assertNotEmitted(output.dispatchOffsetChanged):
        output.apply_offset_button.click()


def test_revert_restores_the_previous_applied_value(qtbot, store):
    output = _make_output(qtbot, store)
    output.set_dispatch_offset_ms(75)
    output.offset_spin.setValue(10)
    output.apply_offset_button.click()

    with qtbot.waitSignal(output.dispatchOffsetChanged, timeout=1000) as blocker:
        output.revert_offset_button.click()

    assert blocker.args == [75]
    assert output.offset_spin.value() == 75


def test_revert_is_unavailable_until_something_is_applied(qtbot, store):
    output = _make_output(qtbot, store)
    assert not output.revert_offset_button.isEnabled()


def test_revert_is_a_single_step_not_a_history(qtbot, store):
    output = _make_output(qtbot, store)
    output.offset_spin.setValue(10)
    output.apply_offset_button.click()
    output.revert_offset_button.click()
    assert not output.revert_offset_button.isEnabled()


# ----------------------------------------------------------------------
# dispatch log
# ----------------------------------------------------------------------

def test_append_events_renders_rows(qtbot, store):
    output = _make_output(qtbot, store)
    output.append_events([
        DispatchEvent(position_ms=1000, kind=SEND, tone="metal", pc=4),
        DispatchEvent(position_ms=2000, kind=HOLD, tone="other"),
    ])
    assert output.log_list.count() == 2
    assert "PC 4" in output.log_list.item(0).text()


def test_log_view_is_capped(qtbot, store):
    output = _make_output(qtbot, store)
    output.append_events([
        DispatchEvent(position_ms=i, kind=SEND, tone="clean", pc=0)
        for i in range(MAX_EVENTS + 25)
    ])
    assert output.log_list.count() == MAX_EVENTS


def test_clear_log_empties_the_view(qtbot, store):
    output = _make_output(qtbot, store)
    output.append_events([DispatchEvent(position_ms=1, kind=SEND, tone="clean", pc=0)])
    output.clear_log_button.click()
    assert output.log_list.count() == 0


def test_active_preset_indicator(qtbot, store):
    output = _make_output(qtbot, store)
    output.set_active_preset(DispatchEvent(position_ms=61500, kind=SEND, tone="metal", pc=4))
    text = output.active_label.text()
    assert "PC 4" in text and "metal" in text and "1:01.500" in text


def test_active_preset_indicator_when_nothing_dispatched(qtbot, store):
    output = _make_output(qtbot, store)
    output.set_active_preset(None)
    assert "—" in output.active_label.text()


# ----------------------------------------------------------------------
# formatting (Qt-free)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("ms,expected", [
    (0, "0:00.000"),
    (1500, "0:01.500"),
    (61500, "1:01.500"),
    (600000, "10:00.000"),
    (-5, "0:00.000"),
])
def test_format_position(ms, expected):
    assert format_position(ms) == expected


def test_format_event_distinguishes_every_kind():
    texts = [
        format_event(DispatchEvent(position_ms=0, kind=SEND, tone="metal", pc=4)),
        format_event(DispatchEvent(position_ms=0, kind=HOLD, tone="other", pc=-1)),
        format_event(DispatchEvent(position_ms=0, kind=GAP, tone=None)),
        format_event(DispatchEvent(position_ms=0, kind=UNMAPPED, tone="ambient")),
    ]
    assert len(set(texts)) == 4
    assert "no preset row" in texts[3]
