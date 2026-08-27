"""Update prompt: offer, download with progress, hand off to the installer."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
)

from guitar_helper.update import UpdateManifest, current_version


def _mb(value: int) -> str:
    return f"{value / (1024 * 1024):.1f} MB"


class UpdatePromptDialog(QDialog):
    """Offer -> download -> ready. The install button only ever becomes live
    after `download_finished`, i.e. after the digest has been verified."""

    downloadRequested = Signal()
    cancelRequested = Signal()
    installRequested = Signal()

    def __init__(self, manifest: UpdateManifest, parent=None) -> None:
        super().__init__(parent)
        self._manifest = manifest
        self._downloading = False
        self._ready = False

        self.setWindowTitle("Update available")

        self.headline = QLabel(
            f"Guitar Helper {manifest.version} is available. You have {current_version()}."
        )
        self.headline.setWordWrap(True)

        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlainText(manifest.notes or "No release notes were provided.")
        self.notes.setMaximumHeight(140)

        self.status = QLabel("")
        self.status.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)

        self.buttons = QDialogButtonBox()
        self.action_button = self.buttons.addButton(
            "&Download", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.later_button = self.buttons.addButton(
            "&Not now", QDialogButtonBox.ButtonRole.RejectRole
        )

        layout = QVBoxLayout(self)
        layout.addWidget(self.headline)
        layout.addWidget(QLabel("What's new:"))
        layout.addWidget(self.notes)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)

        self.action_button.clicked.connect(self._on_action)
        self.later_button.clicked.connect(self._on_later)

        if not manifest.upgradable_from(current_version()):
            self._block_in_place_upgrade()

    def _block_in_place_upgrade(self) -> None:
        self.status.setText(
            f"This build is too old to update automatically "
            f"(needs {self._manifest.min_upgradable_from} or later). "
            f"Download the installer from the releases page instead."
        )
        self.action_button.setEnabled(False)

    # --- worker-driven updates (main thread) ---------------------------------

    def set_progress(self, downloaded: int, total: int) -> None:
        self.progress_bar.setVisible(True)
        if total > 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(downloaded * 100 / total))
            self.status.setText(f"Downloading… {_mb(downloaded)} of {_mb(total)}")
        else:
            # No Content-Length: a busy bar is honest, a made-up percentage is not.
            self.progress_bar.setRange(0, 0)
            self.status.setText(f"Downloading… {_mb(downloaded)}")

    def set_download_finished(self) -> None:
        self._downloading = False
        self._ready = True
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.status.setText(
            "Download verified. Guitar Helper will close while the update installs."
        )
        self.action_button.setText("&Install and restart")
        self.action_button.setEnabled(True)
        self.later_button.setEnabled(True)

    def set_failed(self, message: str) -> None:
        self._downloading = False
        self._ready = False
        self.progress_bar.setVisible(False)
        self.status.setText(f"Update failed: {message}")
        self.action_button.setText("&Retry")
        self.action_button.setEnabled(True)
        self.later_button.setEnabled(True)

    # --- interaction ---------------------------------------------------------

    def _on_action(self) -> None:
        if self._ready:
            self.installRequested.emit()
            return
        self._downloading = True
        self.action_button.setEnabled(False)
        self.later_button.setEnabled(True)
        self.status.setText("Starting download…")
        self.progress_bar.setVisible(True)
        self.downloadRequested.emit()

    def _on_later(self) -> None:
        if self._downloading:
            self.cancelRequested.emit()
        self.reject()

    def reject(self) -> None:
        """Esc and the close button must cancel an in-flight download rather
        than leaving the worker writing to a temp file nobody will collect."""
        if self._downloading:
            self._downloading = False
            self.cancelRequested.emit()
        super().reject()

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt override
        if event.key() == Qt.Key.Key_Escape and self._downloading:
            self.cancelRequested.emit()
        super().keyPressEvent(event)
