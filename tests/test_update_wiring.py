"""MainWindow's update wiring: worker lifecycle and the verified-path invariant."""
from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.ui import main_window as mw  # noqa: E402
from guitar_helper.update import UpdateError  # noqa: E402
from tests.test_ui_smoke import window as ui_smoke_window  # noqa: E402, F401


@pytest.fixture
def window(ui_smoke_window):  # noqa: F811 — the imported fixture is the dependency
    """test_ui_smoke's fixture yields (window, hashes); only the window is needed here."""
    return ui_smoke_window[0]


def test_repeated_checks_do_not_touch_a_deleted_worker(window, qtbot, monkeypatch):
    """A second check after the first finished must not crash.

    The worker is deleteLater'd on finish; if the reference survived, the
    isRunning() guard would be called on a deleted C++ object and raise
    RuntimeError instead of returning False.
    """
    monkeypatch.setattr(
        "guitar_helper.ui.update_worker.check_for_update", lambda *a, **k: None
    )

    window.check_for_updates(silent=True)
    qtbot.waitUntil(lambda: window._update_check is None, timeout=5000)

    # The reference is cleared, so this is a fresh start rather than a probe of
    # a dead wrapper.
    window.check_for_updates(silent=True)
    qtbot.waitUntil(lambda: window._update_check is None, timeout=5000)


def test_check_failure_is_silent_and_clears_the_worker(window, qtbot, monkeypatch):
    def _boom(*a, **k):
        raise UpdateError("no network")

    monkeypatch.setattr("guitar_helper.ui.update_worker.check_for_update", _boom)

    window.check_for_updates(silent=True)
    qtbot.waitUntil(lambda: window._update_check is None, timeout=5000)


def test_install_does_nothing_without_a_verified_download(window, monkeypatch):
    """The install path must only ever launch what download_update returned."""
    launched = []
    monkeypatch.setattr(mw, "launch_installer", lambda path, **k: launched.append(path))

    window._verified_installer = None
    window._install_update()

    assert launched == []


def test_install_launches_only_the_verified_path(window, monkeypatch):
    launched = []
    monkeypatch.setattr(mw, "launch_installer", lambda path, **k: launched.append(path))
    monkeypatch.setattr(mw.QApplication, "quit", lambda *a: None)

    window._verified_installer = r"C:\temp\GuitarHelper-Setup-9.9.9.exe"
    window._install_update()

    assert launched == [r"C:\temp\GuitarHelper-Setup-9.9.9.exe"]


def test_failed_download_clears_the_verified_path(window):
    window._verified_installer = "stale.exe"

    window._on_update_failed("digest mismatch")

    # A previously verified path must not survive a later failure and become
    # launchable by a retry.
    assert window._verified_installer is None


def test_ready_records_the_path_from_the_worker(window):
    window._on_update_ready(r"C:\temp\setup.exe")
    assert window._verified_installer == r"C:\temp\setup.exe"


def test_cancel_is_safe_with_no_download_running(window):
    window._update_download = None
    window._cancel_update_download()  # must not raise
