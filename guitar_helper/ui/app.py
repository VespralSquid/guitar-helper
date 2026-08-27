"""Build QApplication, Application, MainWindow; run()."""
from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from guitar_helper.application import Application
from guitar_helper.config import AppConfig, is_frozen
from guitar_helper.midi.mock_port import MockMidiPort
from guitar_helper.ui import theme
from guitar_helper.ui.main_window import MainWindow

_UPDATE_CHECK_DELAY_MS = 3000


def run(config: AppConfig, *, mock: bool = False) -> int:
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setStyleSheet(theme.APP_QSS)

    port = MockMidiPort() if mock else None
    # A missing MIDI port is no longer fatal — Application falls back to an
    # inert port and MainWindow shows a persistent banner from midi_error.
    application = Application(config, port=port)

    window = MainWindow(application)
    window.resize(1100, 700)
    window.show()

    # Only a frozen build has an installer to apply, and the check is deferred
    # so it can never delay the window appearing. Failures are silent — an
    # offline user must not be nagged on every launch.
    if is_frozen():
        QTimer.singleShot(_UPDATE_CHECK_DELAY_MS, lambda: window.check_for_updates(silent=True))

    return qapp.exec()
