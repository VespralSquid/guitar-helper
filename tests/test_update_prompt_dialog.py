from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.ui.dialogs.update_prompt import UpdatePromptDialog  # noqa: E402
from guitar_helper.update import parse_manifest  # noqa: E402


def _manifest(**overrides):
    doc = {
        "version": "9.9.9",
        "url": "https://example.invalid/GuitarHelper-Setup-9.9.9.exe",
        "sha256": "b" * 64,
        "notes": "Faster analysis.",
    }
    doc.update(overrides)
    return parse_manifest(doc)


def _dialog(qtbot, manifest=None) -> UpdatePromptDialog:
    dialog = UpdatePromptDialog(manifest or _manifest())
    qtbot.addWidget(dialog)
    return dialog


def test_offers_download_first(qtbot):
    dialog = _dialog(qtbot)

    assert "Download" in dialog.action_button.text()
    assert dialog.action_button.isEnabled()
    assert not dialog.progress_bar.isVisible()
    assert "9.9.9" in dialog.headline.text()


def test_release_notes_are_shown(qtbot):
    assert "Faster analysis." in _dialog(qtbot).notes.toPlainText()


def test_missing_notes_fall_back_to_a_placeholder(qtbot):
    dialog = _dialog(qtbot, _manifest(notes=""))
    assert "No release notes" in dialog.notes.toPlainText()


def test_download_click_emits_and_disables_the_button(qtbot):
    dialog = _dialog(qtbot)

    with qtbot.waitSignal(dialog.downloadRequested, timeout=1000):
        dialog.action_button.click()

    assert not dialog.action_button.isEnabled()


def test_progress_with_known_total_is_determinate(qtbot):
    dialog = _dialog(qtbot)

    dialog.set_progress(50 * 1024 * 1024, 100 * 1024 * 1024)

    assert dialog.progress_bar.maximum() == 100
    assert dialog.progress_bar.value() == 50
    assert "50.0 MB of 100.0 MB" in dialog.status.text()


def test_progress_without_content_length_is_indeterminate(qtbot):
    # An invented percentage would be worse than admitting the size is unknown.
    dialog = _dialog(qtbot)

    dialog.set_progress(1024 * 1024, 0)

    assert dialog.progress_bar.maximum() == 0
    assert "1.0 MB" in dialog.status.text()


def test_install_button_only_appears_after_verification(qtbot):
    dialog = _dialog(qtbot)
    dialog.action_button.click()

    assert "Install" not in dialog.action_button.text()

    dialog.set_download_finished()

    assert "Install" in dialog.action_button.text()
    assert dialog.action_button.isEnabled()
    assert "verified" in dialog.status.text().lower()


def test_install_click_emits_install_not_download(qtbot):
    dialog = _dialog(qtbot)
    dialog.action_button.click()
    dialog.set_download_finished()

    with qtbot.waitSignal(dialog.installRequested, timeout=1000):
        dialog.action_button.click()


def test_failure_offers_a_retry(qtbot):
    dialog = _dialog(qtbot)
    dialog.action_button.click()

    dialog.set_failed("digest mismatch")

    assert "Retry" in dialog.action_button.text()
    assert dialog.action_button.isEnabled()
    assert "digest mismatch" in dialog.status.text()
    assert not dialog.progress_bar.isVisible()


def test_retry_after_failure_requests_a_download_again(qtbot):
    dialog = _dialog(qtbot)
    dialog.action_button.click()
    dialog.set_failed("network went away")

    with qtbot.waitSignal(dialog.downloadRequested, timeout=1000):
        dialog.action_button.click()


def test_closing_mid_download_cancels(qtbot):
    dialog = _dialog(qtbot)
    dialog.action_button.click()

    with qtbot.waitSignal(dialog.cancelRequested, timeout=1000):
        dialog.reject()


def test_closing_before_downloading_does_not_cancel(qtbot):
    dialog = _dialog(qtbot)

    with qtbot.assertNotEmitted(dialog.cancelRequested):
        dialog.reject()


def test_too_old_to_upgrade_in_place_blocks_the_button(qtbot):
    dialog = _dialog(qtbot, _manifest(min_upgradable_from="99.0.0"))

    assert not dialog.action_button.isEnabled()
    assert "99.0.0" in dialog.status.text()


# --- GPL source offer shown at the moment a binary is conveyed ----------------


def test_source_url_is_shown_as_a_link(qtbot):
    dialog = _dialog(qtbot, _manifest(source_url="https://example.invalid/src"))

    text = dialog.source_label.text()
    assert "https://example.invalid/src" in text
    assert "GPL" in text
    assert dialog.source_label.openExternalLinks() is True


def test_missing_source_url_still_states_the_licence(qtbot):
    # Silence would be the wrong failure mode: the recipient must always be told
    # the terms, even when the manifest omits where the source lives.
    dialog = _dialog(qtbot)

    text = dialog.source_label.text()
    assert "GPL" in text
    assert "COPYRIGHT" in text
