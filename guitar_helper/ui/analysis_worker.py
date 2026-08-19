"""Runs AnalysisPipeline.analyse() over a file list off the Qt main thread.

The worker owns no store handle and emits AnalysisResult objects; MainWindow
calls pipeline.persist() on the main thread when `fileDone` arrives, exactly
as it calls Application.attach when LoadWorker's `decoded` arrives. SQLite is
the main thread's alone, which is why the skip/analyse `decisions` are
computed there (pipeline.precheck() reads the store) and handed in as a plain
mapping.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from guitar_helper.analysis.pipeline import AnalysisCancelled, AnalysisPipeline

SKIP_REASON = "Already in the library."


def _read_tags(path: Path) -> tuple[str | None, str | None]:
    """Return (title, artist) from file tags; fall back to (stem, None)."""
    try:
        from mutagen import File as MutagenFile  # noqa: PLC0415
        audio = MutagenFile(path, easy=True)
        if audio is not None:
            title  = audio["title"][0]  if "title"  in audio else path.stem
            artist = audio["artist"][0] if "artist" in audio else None
            return title, artist
    except Exception:  # noqa: BLE001
        pass
    return path.stem, None


class AnalysisWorker(QThread):
    progress = Signal(int, int, str, str)  # file_index (1-based), file_total, filename, stage
    fileDone = Signal(object)              # AnalysisResult — persist on the MAIN thread
    fileFailed = Signal(str, str)          # source path, human message
    fileSkipped = Signal(str, str)         # source path, reason

    def __init__(
        self,
        pipeline: AnalysisPipeline,
        paths: Sequence[Path],
        decisions: Mapping[str, bool],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._pipeline = pipeline
        self._paths = [Path(p) for p in paths]
        # Keyed by str(path) as given, not resolved: the main thread built this
        # mapping from the same tuple, so the two must normalise identically.
        self._decisions = dict(decisions)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Thread-safe. Honoured at stage and file boundaries only —
        a separation in flight runs to completion."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        total = len(self._paths)
        for index, path in enumerate(self._paths, start=1):
            if self._cancel.is_set():
                return

            key = str(path)
            if not self._decisions.get(key, True):
                self.fileSkipped.emit(key, SKIP_REASON)
                continue

            title, artist = _read_tags(path)
            try:
                result = self._pipeline.analyse(
                    path,
                    title=title,
                    artist=artist,
                    progress=self._make_progress(index, total, path.name),
                    should_cancel=self._cancel.is_set,
                )
            except AnalysisCancelled:
                return
            except Exception as exc:  # one bad file must not abort the batch
                self.fileFailed.emit(key, str(exc) or exc.__class__.__name__)
                continue

            self.fileDone.emit(result)

    def _make_progress(self, index: int, total: int, filename: str):
        def _emit(stage) -> None:
            self.progress.emit(index, total, filename, str(stage))
        return _emit
