"""Build QApplication, Application, MainWindow; run()."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from guitar_helper.application import Application
from guitar_helper.config import AppConfig
from guitar_helper.midi.mock_port import MockMidiPort
from guitar_helper.ui import theme
from guitar_helper.ui.main_window import MainWindow


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
    return qapp.exec()
