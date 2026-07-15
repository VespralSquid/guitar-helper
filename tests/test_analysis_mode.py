from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.ui.modes.analysis import AnalysisMode  # noqa: E402
from tests.conftest import make_segment  # noqa: E402

TONE_LABELS = ("clean", "crunch", "metal", "edge", "overdrive", "other")


def _make_mode(qtbot) -> AnalysisMode:
    mode = AnalysisMode(TONE_LABELS)
    qtbot.addWidget(mode)
    mode.set_duration_ms(10_000)
    mode.set_segments(
        [
            make_segment("h", 0, 5_000, "clean"),
            make_segment("h", 5_000, 10_000, "metal"),
        ]
    )
    return mode


def test_controls_disabled_with_no_selection(qtbot):
    mode = _make_mode(qtbot)
    assert not mode.relabel_combo.isEnabled()
    assert not mode.confirm_button.isEnabled()
    assert not mode.merge_button.isEnabled()
    assert mode.confirm_all_button.isEnabled()  # segments exist even with no selection


def test_controls_enable_on_selection(qtbot):
    mode = _make_mode(qtbot)
    mode.set_selected(0)
    assert mode.relabel_combo.isEnabled()
    assert mode.confirm_button.isEnabled()
    assert mode.merge_button.isEnabled()


def test_set_selected_syncs_relabel_combo_without_emitting(qtbot):
    mode = _make_mode(qtbot)
    with qtbot.assertNotEmitted(mode.relabelRequested, wait=200):
        mode.set_selected(1)  # segment 1 is "metal"
    assert mode.relabel_combo.currentText() == "metal"


def test_relabel_combo_activation_emits_with_selected_index(qtbot):
    mode = _make_mode(qtbot)
    mode.set_selected(0)
    with qtbot.waitSignal(mode.relabelRequested, timeout=1000) as blocker:
        mode.relabel_combo.textActivated.emit("metal")
    assert blocker.args == [0, "metal"]


def test_confirm_button_emits_with_selected_index(qtbot):
    mode = _make_mode(qtbot)
    mode.set_selected(1)
    with qtbot.waitSignal(mode.confirmRequested, timeout=1000) as blocker:
        mode.confirm_button.click()
    assert blocker.args == [1]


def test_confirm_all_button_emits(qtbot):
    mode = _make_mode(qtbot)
    with qtbot.waitSignal(mode.confirmAllRequested, timeout=1000):
        mode.confirm_all_button.click()


def test_merge_button_emits_with_selected_index(qtbot):
    mode = _make_mode(qtbot)
    mode.set_selected(0)
    with qtbot.waitSignal(mode.mergeRequested, timeout=1000) as blocker:
        mode.merge_button.click()
    assert blocker.args == [0]


def test_exclude_checkbox_toggle_emits(qtbot):
    mode = _make_mode(qtbot)
    with qtbot.waitSignal(mode.excludeToggled, timeout=1000) as blocker:
        mode.exclude_checkbox.click()
    assert blocker.args == [True]


def test_set_excluded_does_not_emit_exclude_toggled(qtbot):
    mode = _make_mode(qtbot)
    with qtbot.assertNotEmitted(mode.excludeToggled, wait=200):
        mode.set_excluded(True)
    assert mode.exclude_checkbox.isChecked()


def test_save_and_discard_buttons_disabled_until_dirty(qtbot):
    mode = _make_mode(qtbot)
    assert not mode.save_button.isEnabled()
    assert not mode.discard_button.isEnabled()

    mode.set_dirty(True)
    assert mode.save_button.isEnabled()
    assert mode.discard_button.isEnabled()
    assert mode.dirty_label.text()

    mode.set_dirty(False)
    assert not mode.save_button.isEnabled()
    assert not mode.discard_button.isEnabled()
    assert not mode.dirty_label.text()


def test_save_button_emits_save_requested(qtbot):
    mode = _make_mode(qtbot)
    mode.set_dirty(True)
    with qtbot.waitSignal(mode.saveRequested, timeout=1000):
        mode.save_button.click()


def test_discard_button_emits_discard_requested(qtbot):
    mode = _make_mode(qtbot)
    mode.set_dirty(True)
    with qtbot.waitSignal(mode.discardRequested, timeout=1000):
        mode.discard_button.click()


def test_timeline_boundary_edit_forwarded(qtbot):
    mode = _make_mode(qtbot)
    with qtbot.waitSignal(mode.boundaryEditRequested, timeout=1000) as blocker:
        mode.timeline.boundaryEditRequested.emit(0, 4_500)
    assert blocker.args == [0, 4_500]


# =========================================================================
# playlist header / prev / next
# =========================================================================

def test_prev_next_disabled_by_default(qtbot):
    mode = _make_mode(qtbot)
    assert not mode.prev_song_button.isEnabled()
    assert not mode.next_song_button.isEnabled()
    assert mode.header_label.text() == ""


def test_set_playlist_context_updates_header_and_buttons(qtbot):
    mode = _make_mode(qtbot)
    mode.set_playlist_context("Practice — Song A (2 of 5)", True, True)
    assert mode.header_label.text() == "Practice — Song A (2 of 5)"
    assert mode.prev_song_button.isEnabled()
    assert mode.next_song_button.isEnabled()


def test_prev_song_button_emits(qtbot):
    mode = _make_mode(qtbot)
    mode.set_playlist_context("x", True, True)
    with qtbot.waitSignal(mode.analyzePrevRequested, timeout=1000):
        mode.prev_song_button.click()


def test_next_song_button_emits(qtbot):
    mode = _make_mode(qtbot)
    mode.set_playlist_context("x", True, True)
    with qtbot.waitSignal(mode.analyzeNextRequested, timeout=1000):
        mode.next_song_button.click()
