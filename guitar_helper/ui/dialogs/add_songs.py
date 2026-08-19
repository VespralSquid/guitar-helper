"""Add-songs dialog: pick files and folders, show the preflight verdict, and
return the ingestion options.

Folder expansion happens here, so `AddSongsOptions.paths` reaching the worker
is always a flat tuple of real, supported, readable files. preflight() is
find_spec-based and cheap, so running it on every selection change is safe on
the main thread; the deep=True variant permanently loads torch and is never
called from Qt.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from guitar_helper.analysis.environment import SUPPORTED_SUFFIXES, EnvironmentReport, preflight
from guitar_helper.config import AppConfig

PAUSE_PLAYBACK_SETTING = "pause_playback_during_analysis"

_NO_PLAYLIST = "(none)"
_NAME_FILTER = "Audio files ({})".format(
    " ".join(f"*{suffix}" for suffix in sorted(SUPPORTED_SUFFIXES))
)


@dataclass(frozen=True)
class AddSongsOptions:
    paths: tuple[Path, ...]          # already expanded; folders resolved by the dialog
    reanalyse_existing: bool         # plan 2.5 - never overrides manual corrections
    target_playlist_id: int | None   # add each analysed track to this playlist
    pause_playback: bool             # decision D3


def expand_audio_files(paths: Iterable[str | Path], *, recursive: bool) -> list[Path]:
    """Flatten a mixed files/folders selection to supported audio files,
    de-duplicated and order-preserving."""
    out: list[Path] = []
    seen: set[Path] = set()

    def _append(candidate: Path) -> None:
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)

    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            for found in sorted(path.glob(pattern)):
                if found.is_file() and found.suffix.lower() in SUPPORTED_SUFFIXES:
                    _append(found)
        else:
            _append(path)
    return out


class AddSongsDialog(QDialog):

    def __init__(
        self,
        store,
        config: AppConfig,
        *,
        preselected_playlist_id: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add songs")
        self._store = store
        self._config = config
        self._selection: list[Path] = []
        self._usable: tuple[Path, ...] = ()
        self._report = EnvironmentReport()
        self._options: AddSongsOptions | None = None

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.add_files_button = QPushButton("Add files...")
        self.add_folder_button = QPushButton("Add folder...")
        self.remove_button = QPushButton("Remove selected")

        self.recursive_check = QCheckBox("Include subfolders")
        self.reanalyse_check = QCheckBox("Re-analyse songs already in the library")
        self.reanalyse_check.setToolTip(
            "Songs with manual corrections are always skipped, even when this is ticked."
        )
        self.pause_check = QCheckBox("Pause playback while analysing")
        self.pause_check.setChecked(self._stored_pause_playback())

        self.playlist_combo = QComboBox()
        self._populate_playlists(preselected_playlist_id)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.rejected_list = QListWidget()
        self.rejected_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText("Analyse")

        picker_row = QHBoxLayout()
        picker_row.addWidget(self.add_files_button)
        picker_row.addWidget(self.add_folder_button)
        picker_row.addWidget(self.remove_button)
        picker_row.addStretch(1)

        files_box = QGroupBox("Songs to analyse")
        files_layout = QVBoxLayout(files_box)
        files_layout.addLayout(picker_row)
        files_layout.addWidget(self.file_list, stretch=1)
        files_layout.addWidget(self.recursive_check)

        options_box = QGroupBox("Options")
        options_layout = QFormLayout(options_box)
        options_layout.addRow("Add to playlist", self.playlist_combo)
        options_layout.addRow(self.reanalyse_check)
        options_layout.addRow(self.pause_check)

        report_box = QGroupBox("Preflight")
        report_layout = QVBoxLayout(report_box)
        report_layout.addWidget(self.status_label)
        report_layout.addWidget(self.rejected_list, stretch=1)

        layout = QVBoxLayout(self)
        layout.addWidget(files_box, stretch=2)
        layout.addWidget(options_box)
        layout.addWidget(report_box, stretch=1)
        layout.addWidget(self.buttons)

        self.add_files_button.clicked.connect(self._on_add_files)
        self.add_folder_button.clicked.connect(self._on_add_folder)
        self.remove_button.clicked.connect(self._on_remove_selected)
        self.recursive_check.toggled.connect(self._on_recursive_toggled)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        self.refresh()

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------

    def add_paths(self, paths: Sequence[str | Path]) -> None:
        """Add files and/or folders. Folders are kept as folders, so toggling
        "Include subfolders" re-expands them instead of needing a re-pick."""
        for raw in paths:
            path = Path(raw)
            if path not in self._selection:
                self._selection.append(path)
        self.refresh()

    def selected_paths(self) -> tuple[Path, ...]:
        """What the user picked, before expansion - folders included."""
        return tuple(self._selection)

    def _on_add_files(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, "Add songs", "", _NAME_FILTER)
        if chosen:
            self.add_paths(chosen)

    def _on_add_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Add a folder of songs")
        if chosen:
            self.add_paths([chosen])

    def _on_remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            path = Path(item.text())
            if path in self._selection:
                self._selection.remove(path)
        self.refresh()

    def _on_recursive_toggled(self, _checked: bool) -> None:
        self.refresh()

    # ------------------------------------------------------------------
    # preflight
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        candidates = expand_audio_files(
            self._selection, recursive=self.recursive_check.isChecked()
        )
        report = preflight(
            candidates,
            stems_dir=self._config.stems_dir,
            db_path=self._config.db_path,
            model_dir=self._config.model_dir,
        )
        self._report = report
        self._usable = tuple(report.usable_files)

        self.file_list.clear()
        self.file_list.addItems([str(p) for p in self._selection])

        self.rejected_list.clear()
        for check in report.blockers:
            self.rejected_list.addItem(f"Blocked - {check.detail} {check.remedy}".strip())
        for check in report.warnings:
            self.rejected_list.addItem(f"Warning - {check.detail} {check.remedy}".strip())
        for rejected in report.rejected_files:
            self.rejected_list.addItem(f"{rejected.path.name} - {rejected.detail}")

        self.status_label.setText(self._status_text(report, len(candidates)))
        self.ok_button.setEnabled(report.can_proceed)

    def report(self) -> EnvironmentReport:
        """The verdict behind the current enabled/disabled state of Analyse."""
        return self._report

    def _status_text(self, report: EnvironmentReport, candidate_count: int) -> str:
        if report.blockers:
            return "Cannot analyse: the environment is not ready. See below."
        if not self._selection:
            return "Choose the songs or folders you want to analyse."
        if not report.usable_files:
            return "None of the selected files can be analysed. See below."
        excluded = candidate_count - len(report.usable_files)
        text = f"{len(report.usable_files)} song(s) ready to analyse."
        if excluded:
            text += f" {excluded} excluded."
        return text

    # ------------------------------------------------------------------
    # result
    # ------------------------------------------------------------------

    def options(self) -> AddSongsOptions | None:
        """The accepted options, or None if the dialog was cancelled or had
        nothing usable to offer. A plain getter - it never execs."""
        return self._options

    def accept(self) -> None:
        if not self._report.can_proceed:
            return
        self._store.set_setting(
            PAUSE_PLAYBACK_SETTING, "1" if self.pause_check.isChecked() else "0"
        )
        self._options = AddSongsOptions(
            paths=self._usable,
            reanalyse_existing=self.reanalyse_check.isChecked(),
            target_playlist_id=self.playlist_combo.currentData(),
            pause_playback=self.pause_check.isChecked(),
        )
        super().accept()

    # ------------------------------------------------------------------
    # store-backed widgets
    # ------------------------------------------------------------------

    def _stored_pause_playback(self) -> bool:
        return self._store.get_setting(PAUSE_PLAYBACK_SETTING) == "1"

    def _populate_playlists(self, preselected_playlist_id: int | None) -> None:
        self.playlist_combo.addItem(_NO_PLAYLIST, None)
        for playlist in self._store.list_playlists():
            self.playlist_combo.addItem(playlist.name, playlist.id)
            if playlist.id == preselected_playlist_id:
                self.playlist_combo.setCurrentIndex(self.playlist_combo.count() - 1)
