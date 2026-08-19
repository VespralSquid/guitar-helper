"""Analysis progress dialog: stage + n-of-m, a per-file result list, and Cancel.

Progress is stage-level and honest rather than a fake percentage: the separator
is a single blocking third-party call with no progress hook, and it is also the
only stage that takes minutes, so it carries an explicit note saying so.

Cancel is honoured at stage and file boundaries only (plan 2.3). Pressing it
therefore cannot stop a separation already in flight, so the button disables
itself and the dialog says "Finishing current song..." - a dialog that looked
frozen would be indistinguishable from a hang.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QProgressBar,
    QVBoxLayout,
)

from guitar_helper.analysis.pipeline import Stage

CANCELLING_TEXT = "Finishing current song..."

_STAGE_TEXT: dict[str, str] = {
    Stage.HASHING.value: "Identifying the file...",
    Stage.SEPARATING.value: "Separating the guitar stem - this can take several minutes...",
    Stage.DECODING.value: "Decoding the stem...",
    Stage.FEATURES.value: "Extracting features...",
    Stage.SEGMENTING.value: "Finding segment boundaries...",
    Stage.CLASSIFYING.value: "Classifying tones...",
    Stage.SAVING.value: "Saving to the library...",
}

_OUTCOME_TEXT = {"done": "Analysed", "failed": "Failed", "skipped": "Skipped"}


def stage_text(stage: str) -> str:
    return _STAGE_TEXT.get(str(stage), f"{str(stage).replace('_', ' ').capitalize()}...")


class AnalysisProgressDialog(QDialog):
    cancelRequested = Signal()

    def __init__(self, total_files: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Analysing songs")
        self._total = max(0, int(total_files))
        self._cancelling = False
        self._finished = False
        self._counts = {"done": 0, "failed": 0, "skipped": 0}

        self.file_label = QLabel(self._file_text(0, self._total, ""))
        self.file_label.setWordWrap(True)
        self.stage_label = QLabel("Starting...")
        self.stage_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, self._total)
        self.progress_bar.setValue(0)

        self.result_list = QListWidget()
        self.result_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self.file_label)
        layout.addWidget(self.stage_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.result_list, stretch=1)
        layout.addWidget(self.buttons)

        self.buttons.rejected.connect(self._on_cancel)

    # ------------------------------------------------------------------
    # worker-driven updates (main thread, via queued signal connections)
    # ------------------------------------------------------------------

    def set_progress(self, file_index: int, file_total: int, filename: str, stage: str) -> None:
        self._total = file_total
        self.progress_bar.setRange(0, file_total)
        self.progress_bar.setValue(max(0, file_index - 1))
        self.file_label.setText(self._file_text(file_index, file_total, filename))
        if not self._cancelling and not self._finished:
            self.stage_label.setText(stage_text(stage))

    def add_result(self, filename: str, outcome: str, detail: str = "") -> None:
        if outcome in self._counts:
            self._counts[outcome] += 1
        label = _OUTCOME_TEXT.get(outcome, outcome)
        line = f"{label}: {filename}"
        if detail:
            line += f" - {detail}"
        self.result_list.addItem(line)
        self.progress_bar.setValue(min(self.progress_bar.maximum(), sum(self._counts.values())))

    def set_finished(self) -> None:
        if not self._finished:
            self.buttons.rejected.disconnect(self._on_cancel)
            self.buttons.rejected.connect(self.accept)
        self._finished = True
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.stage_label.setText(self._summary_text())
        self.file_label.setText("Cancelled." if self._cancelling else "Finished.")
        self.cancel_button.setText("Close")
        self.cancel_button.setEnabled(True)

    # ------------------------------------------------------------------
    # cancellation
    # ------------------------------------------------------------------

    @property
    def cancelling(self) -> bool:
        return self._cancelling

    def _on_cancel(self) -> None:
        if self._cancelling:
            return
        self._cancelling = True
        self.cancel_button.setEnabled(False)
        self.stage_label.setText(CANCELLING_TEXT)
        self.cancelRequested.emit()

    def reject(self) -> None:
        """Esc and the window close button must request cancellation rather
        than tearing the dialog out from under a running worker."""
        if self._finished:
            super().reject()
            return
        self._on_cancel()

    # ------------------------------------------------------------------

    def _file_text(self, file_index: int, file_total: int, filename: str) -> str:
        if not filename:
            return f"Preparing {file_total} song(s)..."
        return f"Song {file_index} of {file_total} - {filename}"

    def _summary_text(self) -> str:
        return (
            f"{self._counts['done']} analysed, "
            f"{self._counts['failed']} failed, "
            f"{self._counts['skipped']} skipped."
        )
