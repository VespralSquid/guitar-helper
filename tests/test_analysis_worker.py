from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from guitar_helper.analysis.pipeline import (  # noqa: E402
    ANALYSE_STAGES,
    AnalysisCancelled,
    AnalysisResult,
    Stage,
)
from guitar_helper.ui.analysis_worker import AnalysisWorker  # noqa: E402
from tests.conftest import make_segment  # noqa: E402


def _result(path: Path) -> AnalysisResult:
    file_hash = f"hash-{path.stem}"
    return AnalysisResult(
        file_hash=file_hash,
        duration_ms=1000,
        filename=path.name,
        source_path=str(path),
        stem_path=str(path.with_name(f"{file_hash}_guitar.wav")),
        title=path.stem,
        artist=None,
        segments=[make_segment(file_hash, 0, 1000, "clean")],
    )


class StubPipeline:
    """Canned analyse(); precheck/persist are traps — the worker must never
    reach the store."""

    def __init__(self, *, error: Exception | None = None, on_analyse=None) -> None:
        self.error = error
        self.on_analyse = on_analyse
        self.analysed: list[Path] = []
        self.kwargs: list[dict] = []

    def analyse(self, path, *, title=None, artist=None, k=None,
                progress=None, should_cancel=None) -> AnalysisResult:
        path = Path(path)
        self.analysed.append(path)
        self.kwargs.append({"title": title, "artist": artist})
        for stage in ANALYSE_STAGES:
            if should_cancel is not None and should_cancel():
                raise AnalysisCancelled(path, stage)
            if progress is not None:
                progress(stage)
        if self.on_analyse is not None:
            self.on_analyse(path)
        if self.error is not None:
            raise self.error
        return _result(path)

    def precheck(self, path):  # pragma: no cover - must never run on the worker
        raise AssertionError("worker called precheck()")

    def persist(self, result, **kwargs):  # pragma: no cover
        raise AssertionError("worker called persist()")


class _Recorder:
    def __init__(self, worker: AnalysisWorker) -> None:
        self.progress: list[tuple] = []
        self.done: list[AnalysisResult] = []
        self.failed: list[tuple[str, str]] = []
        self.skipped: list[tuple[str, str]] = []
        worker.progress.connect(lambda *a: self.progress.append(a))
        worker.fileDone.connect(self.done.append)
        worker.fileFailed.connect(lambda *a: self.failed.append(a))
        worker.fileSkipped.connect(lambda *a: self.skipped.append(a))


def _files(tmp_path, *names: str) -> list[Path]:
    out = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"not really audio")
        out.append(path)
    return out


def _run(qtbot, worker: AnalysisWorker) -> _Recorder:
    recorder = _Recorder(worker)
    with qtbot.waitSignal(worker.finished, timeout=5000):
        worker.start()
    return recorder


def test_emits_file_done_for_every_analysed_file(qtbot, tmp_path):
    paths = _files(tmp_path, "a.wav", "b.wav")
    pipeline = StubPipeline()
    worker = AnalysisWorker(pipeline, paths, {str(p): True for p in paths})

    recorder = _run(qtbot, worker)

    assert [r.filename for r in recorder.done] == ["a.wav", "b.wav"]
    assert pipeline.analysed == paths


def test_progress_reports_index_total_filename_and_stage(qtbot, tmp_path):
    paths = _files(tmp_path, "a.wav", "b.wav")
    worker = AnalysisWorker(StubPipeline(), paths, {str(p): True for p in paths})

    recorder = _run(qtbot, worker)

    assert recorder.progress[0] == (1, 2, "a.wav", Stage.HASHING.value)
    first_file = [p for p in recorder.progress if p[0] == 1]
    assert [p[3] for p in first_file] == [s.value for s in ANALYSE_STAGES]
    assert recorder.progress[-1][0] == 2


def test_one_bad_file_does_not_abort_the_batch(qtbot, tmp_path):
    paths = _files(tmp_path, "bad.wav", "good.wav")

    class FailFirst(StubPipeline):
        def analyse(self, path, **kwargs):
            if Path(path).name == "bad.wav":
                self.analysed.append(Path(path))
                raise RuntimeError("separation blew up")
            return super().analyse(path, **kwargs)

    pipeline = FailFirst()
    worker = AnalysisWorker(pipeline, paths, {str(p): True for p in paths})

    recorder = _run(qtbot, worker)

    assert recorder.failed == [(str(paths[0]), "separation blew up")]
    assert [r.filename for r in recorder.done] == ["good.wav"]


def test_failure_message_falls_back_to_the_exception_type(qtbot, tmp_path):
    paths = _files(tmp_path, "a.wav")
    worker = AnalysisWorker(StubPipeline(error=RuntimeError()), paths, {str(paths[0]): True})

    recorder = _run(qtbot, worker)

    assert recorder.failed == [(str(paths[0]), "RuntimeError")]


def test_a_false_decision_skips_without_analysing(qtbot, tmp_path):
    paths = _files(tmp_path, "known.wav", "new.wav")
    pipeline = StubPipeline()
    decisions = {str(paths[0]): False, str(paths[1]): True}
    worker = AnalysisWorker(pipeline, paths, decisions)

    recorder = _run(qtbot, worker)

    assert pipeline.analysed == [paths[1]]
    assert [s[0] for s in recorder.skipped] == [str(paths[0])]
    assert recorder.skipped[0][1]


def test_a_missing_decision_defaults_to_analysing(qtbot, tmp_path):
    paths = _files(tmp_path, "a.wav")
    pipeline = StubPipeline()
    worker = AnalysisWorker(pipeline, paths, {})

    _run(qtbot, worker)

    assert pipeline.analysed == paths


def test_cancel_before_start_analyses_nothing(qtbot, tmp_path):
    paths = _files(tmp_path, "a.wav", "b.wav")
    pipeline = StubPipeline()
    worker = AnalysisWorker(pipeline, paths, {str(p): True for p in paths})
    worker.cancel()

    recorder = _run(qtbot, worker)

    assert worker.cancelled is True
    assert pipeline.analysed == []
    assert recorder.done == []


def test_cancel_mid_batch_stops_before_the_next_file(qtbot, tmp_path):
    paths = _files(tmp_path, "a.wav", "b.wav", "c.wav")
    holder: dict[str, AnalysisWorker] = {}
    pipeline = StubPipeline(on_analyse=lambda _p: holder["worker"].cancel())
    worker = AnalysisWorker(pipeline, paths, {str(p): True for p in paths})
    holder["worker"] = worker

    recorder = _run(qtbot, worker)

    assert pipeline.analysed == [paths[0]]
    assert [r.filename for r in recorder.done] == ["a.wav"]
    assert worker.cancelled is True


def test_cancel_at_a_stage_boundary_is_not_reported_as_a_failure(qtbot, tmp_path):
    paths = _files(tmp_path, "a.wav")
    holder: dict[str, AnalysisWorker] = {}

    class CancelDuringStages(StubPipeline):
        def analyse(self, path, *, progress=None, should_cancel=None, **kwargs):
            holder["worker"].cancel()
            return super().analyse(
                path, progress=progress, should_cancel=should_cancel, **kwargs
            )

    worker = AnalysisWorker(CancelDuringStages(), paths, {str(paths[0]): True})
    holder["worker"] = worker

    recorder = _run(qtbot, worker)

    assert recorder.failed == []
    assert recorder.done == []


def test_worker_reads_tags_for_title_and_artist(qtbot, tmp_path):
    paths = _files(tmp_path, "song.wav")
    pipeline = StubPipeline()
    worker = AnalysisWorker(pipeline, paths, {str(paths[0]): True})

    _run(qtbot, worker)

    assert pipeline.kwargs[0]["title"] == "song"


def test_worker_never_touches_precheck_or_persist(qtbot, tmp_path):
    paths = _files(tmp_path, "a.wav")
    worker = AnalysisWorker(StubPipeline(), paths, {str(paths[0]): True})

    recorder = _run(qtbot, worker)

    assert recorder.failed == []
    assert len(recorder.done) == 1
