"""Runs Application.decode (hash + full librosa decode) off the Qt main
thread so loading a track never freezes the GUI. The worker touches no DB
and no playback objects; MainWindow calls Application.attach on the main
thread when `decoded` arrives."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from guitar_helper.application import Application


class LoadWorker(QThread):
    decoded = Signal(str, object, str)  # file_hash, AudioBuffer, display name
    failed = Signal(str)

    def __init__(self, application: Application, path: str, parent=None) -> None:
        super().__init__(parent)
        self._app = application
        self._path = path

    def run(self) -> None:
        try:
            file_hash, buffer = self._app.decode(self._path)
        except Exception as exc:  # decode failures must surface, not kill the thread
            self.failed.emit(f"Could not load {self._path!r}: {exc}")
            return
        self.decoded.emit(file_hash, buffer, Path(self._path).name)
