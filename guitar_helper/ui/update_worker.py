"""Qt surface over guitar_helper.update.

Two threads, deliberately separate: the launch-time check is silent and must
never delay or block the window appearing, while the download is user-initiated
and reports progress. Neither touches the store or the audio path.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from guitar_helper.update import (
    UpdateError,
    UpdateManifest,
    check_for_update,
    download_update,
)


class UpdateCheckWorker(QThread):
    """Fetches the manifest. Failure is silent by design — an offline user must
    not be nagged about an unreachable update server on every launch."""

    updateAvailable = Signal(object)  # UpdateManifest
    upToDate = Signal()
    failed = Signal(str)

    def __init__(self, url: str | None = None, current: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self._url = url
        self._current = current

    def run(self) -> None:
        try:
            manifest = check_for_update(self._url, current=self._current)
        except UpdateError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — a check must never crash the app
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return

        if manifest is None:
            self.upToDate.emit()
        else:
            self.updateAvailable.emit(manifest)


class UpdateDownloadWorker(QThread):
    """Downloads and verifies. `ready` carries a path that has already been
    checked against the manifest digest; nothing else may launch it."""

    progress = Signal(int, int)   # bytes downloaded, total (0 when unknown)
    ready = Signal(str)           # verified installer path
    failed = Signal(str)

    def __init__(self, manifest: UpdateManifest, dest_dir: str | Path | None = None, parent=None):
        super().__init__(parent)
        self._manifest = manifest
        self._dest_dir = dest_dir
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        try:
            path = download_update(
                self._manifest,
                dest_dir=self._dest_dir,
                progress=lambda done, total: self.progress.emit(done, total),
                should_cancel=self._cancel.is_set,
            )
        except UpdateError as exc:
            if not self._cancel.is_set():
                self.failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return

        self.ready.emit(str(path))
