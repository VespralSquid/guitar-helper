from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.analysis.pipeline import Stage  # noqa: E402
from guitar_helper.ui.dialogs.analysis_progress import (  # noqa: E402
    CANCELLING_TEXT,
    AnalysisProgressDialog,
)


def _dialog(qtbot, total: int = 2) -> AnalysisProgressDialog:
    dialog = AnalysisProgressDialog(total)
    qtbot.addWidget(dialog)
    return dialog


def _lines(dialog) -> list[str]:
    return [dialog.result_list.item(i).text() for i in range(dialog.result_list.count())]


def test_starts_with_cancel_enabled_and_no_results(qtbot):
    dialog = _dialog(qtbot)

    assert dialog.cancel_button.isEnabled()
    assert dialog.cancel_button.text() == "Cancel"
    assert _lines(dialog) == []


def test_set_progress_shows_n_of_m_and_the_filename(qtbot):
    dialog = _dialog(qtbot, 3)

    dialog.set_progress(2, 3, "song.wav", Stage.DECODING.value)

    assert "2" in dialog.file_label.text()
    assert "3" in dialog.file_label.text()
    assert "song.wav" in dialog.file_label.text()


def test_separating_warns_that_it_takes_minutes(qtbot):
    dialog = _dialog(qtbot)

    dialog.set_progress(1, 1, "song.wav", Stage.SEPARATING.value)

    assert "several minutes" in dialog.stage_label.text()


def test_every_stage_has_human_text(qtbot):
    dialog = _dialog(qtbot)

    for stage in Stage:
        dialog.set_progress(1, 1, "song.wav", stage.value)
        assert dialog.stage_label.text() != stage.value


def test_unknown_stage_degrades_gracefully(qtbot):
    dialog = _dialog(qtbot)

    dialog.set_progress(1, 1, "song.wav", "reticulating_splines")

    assert "Reticulating splines" in dialog.stage_label.text()


def test_progress_bar_tracks_the_batch(qtbot):
    dialog = _dialog(qtbot, 4)

    dialog.set_progress(3, 4, "c.wav", Stage.HASHING.value)

    assert dialog.progress_bar.maximum() == 4
    assert dialog.progress_bar.value() == 2


def test_add_result_lists_outcome_and_detail(qtbot):
    dialog = _dialog(qtbot)

    dialog.add_result("a.wav", "done")
    dialog.add_result("b.wav", "failed", "separation blew up")
    dialog.add_result("c.wav", "skipped", "Already in the library.")

    lines = _lines(dialog)
    assert lines[0] == "Analysed: a.wav"
    assert lines[1] == "Failed: b.wav - separation blew up"
    assert lines[2] == "Skipped: c.wav - Already in the library."


# ---------------------------------------------------------------------------
# cancellation must never look like a freeze
# ---------------------------------------------------------------------------

def test_cancel_emits_disables_and_says_finishing_current_song(qtbot):
    dialog = _dialog(qtbot)

    with qtbot.waitSignal(dialog.cancelRequested, timeout=1000):
        dialog.cancel_button.click()

    assert dialog.cancelling is True
    assert not dialog.cancel_button.isEnabled()
    assert dialog.stage_label.text() == CANCELLING_TEXT


def test_cancel_is_emitted_only_once(qtbot):
    dialog = _dialog(qtbot)
    emitted = []
    dialog.cancelRequested.connect(lambda: emitted.append(1))

    dialog.cancel_button.click()
    dialog.reject()

    assert emitted == [1]


def test_late_progress_does_not_overwrite_the_cancelling_message(qtbot):
    dialog = _dialog(qtbot)
    dialog.cancel_button.click()

    dialog.set_progress(2, 2, "b.wav", Stage.SEPARATING.value)

    assert dialog.stage_label.text() == CANCELLING_TEXT


def test_escape_requests_cancellation_instead_of_closing(qtbot):
    dialog = _dialog(qtbot)
    dialog.show()
    qtbot.waitExposed(dialog)

    with qtbot.waitSignal(dialog.cancelRequested, timeout=1000):
        dialog.reject()

    assert dialog.isVisible()
    assert dialog.cancelling is True


# ---------------------------------------------------------------------------
# completion
# ---------------------------------------------------------------------------

def test_set_finished_turns_cancel_into_close(qtbot):
    dialog = _dialog(qtbot)

    dialog.set_finished()

    assert dialog.cancel_button.text() == "Close"
    assert dialog.cancel_button.isEnabled()


def test_set_finished_re_enables_close_after_a_cancel(qtbot):
    dialog = _dialog(qtbot)
    dialog.cancel_button.click()

    dialog.set_finished()

    assert dialog.cancel_button.isEnabled()
    assert "Cancelled" in dialog.file_label.text()


def test_close_after_finish_closes_without_re_emitting_cancel(qtbot):
    dialog = _dialog(qtbot)
    dialog.set_finished()
    emitted = []
    dialog.cancelRequested.connect(lambda: emitted.append(1))

    dialog.cancel_button.click()

    assert emitted == []
    assert dialog.result() == int(dialog.DialogCode.Accepted)


def test_finished_summary_counts_every_outcome(qtbot):
    dialog = _dialog(qtbot, 3)
    dialog.add_result("a.wav", "done")
    dialog.add_result("b.wav", "failed", "boom")
    dialog.add_result("c.wav", "skipped", "known")

    dialog.set_finished()

    text = dialog.stage_label.text()
    assert "1 analysed" in text
    assert "1 failed" in text
    assert "1 skipped" in text


def test_set_finished_is_idempotent(qtbot):
    dialog = _dialog(qtbot)

    dialog.set_finished()
    dialog.set_finished()

    assert dialog.cancel_button.text() == "Close"
