# Wave 2 orchestration — MVP Gate 3 completion, Gate 4, Gate 6

**Written:** 2026-08-19. **Restore point:** git tag `mvp-wave2-restore-point` at `ba29137`;
live DB copied to `.backup-mvp-wave2/library.db.bak` (9 tracks / 123 segments / 42
calibration / 8 playlist rows / schema v10).

**Authority:** `docs/plans/mvp-implementation-plan.md` for scope and ordering,
`docs/plans/gui-analysis-pipeline-plan.md` for Gate 3 design. This document adds only
what parallel execution needs: who owns which files, and the contracts between lanes.

## 1. What is already done (do not rebuild it)

Commit `922bf00` landed more of Gate 3 than `TODO.md` implies. Verified in the tree:

| Plan step | State |
|---|---|
| §5 step 1 — `analyse` / `persist` / `run` split | **Done.** `AnalysisResult`, `TrackPrecheck`, `precheck()` all exist in `analysis/pipeline.py` |
| §5 step 2 — `progress` + `should_cancel` | **Done.** `Stage` StrEnum, `ANALYSE_STAGES`, `AnalysisCancelled` |
| §5 step 0a — separator required | **Done.** `AnalysisPipeline.__init__` takes keyword-only `separator` |
| §5 step 3 — `analysis/environment.py` preflight | **Done.** `preflight()`, `EnvironmentReport`, `Check`, `FileCheck` |

Remaining: step 0c (`delete_track`), steps 4–7 (worker, dialogs, wiring), Gate 4, Gate 6.

## 2. Lanes and file ownership

**The ownership rule:** edit only the files your lane owns. Not a one-line import fix in
another lane's file, not an obvious typo, not a test elsewhere. If your work requires a
change outside your boundary, stop and report it — the orchestrator makes it.

| Lane | Deliverable | Owns |
|---|---|---|
| **A — store** | `delete_track` (plan §4.3 / step 0c) | `guitar_helper/db/interfaces.py`, `guitar_helper/db/repository.py`, `tests/test_repository.py` |
| **B — Qt ingestion** | worker, both dialogs, pipeline factory (steps 4–5) | **new files only:** `guitar_helper/analysis/factory.py`, `guitar_helper/ui/analysis_worker.py`, `guitar_helper/ui/dialogs/__init__.py`, `guitar_helper/ui/dialogs/add_songs.py`, `guitar_helper/ui/dialogs/analysis_progress.py`, `tests/test_analysis_factory.py`, `tests/test_analysis_worker.py`, `tests/test_add_songs_dialog.py`, `tests/test_analysis_progress_dialog.py` |
| **C — robustness** | Gate 4 **H1** (port fallback half) and **H3** | `guitar_helper/ui/app.py`, `guitar_helper/application.py`, `guitar_helper/midi/mido_port.py`, `guitar_helper/correction/cli.py`, `tests/test_application.py`, `tests/test_midi_fallback.py` (new), `tests/test_correction_cli.py` (new if absent) |
| **D — docs** | Gate 6 user guide + README | `README.md` (new), `LICENSE` (new), `docs/user-guide.md` (new) |
| **Orchestrator (not an agent)** | integration, Gate 4 **H2**/**H5**, MIDI banner, Help menu, Home wiring, `SAVE_STATE.md`, `TODO.md` | `guitar_helper/ui/main_window.py`, `guitar_helper/ui/modes/home.py`, `tests/test_home_mode.py`, `tests/test_ui_smoke.py`, `SAVE_STATE.md`, `TODO.md`, `docs/plans/*` |

`main_window.py` and `modes/home.py` are orchestrator-only because every lane's work
converges there. That is why H2 (main_window) and H5 (home) are not in Lane C.

## 3. Frozen contracts

Changing any signature below breaks another lane. If one must change: **stop and report.**

### 3.1 Track deletion (Lane A)

```python
class ITrackEditor(ABC):
    """Track removal. Separate from ITrackCatalog because deletion is
    destructive and only the library UI needs it."""

    @abstractmethod
    def delete_track(self, file_hash: str) -> None:
        """Remove a track and everything that references it, in one transaction:
        segments, segments_calibration, playlist_tracks, then the tracks row.
        Unknown file_hash is a no-op, not an error. The cached stem is NOT
        touched (decision D5) — re-adding the file skips separation."""
```

- `ITrackEditor` is mixed into **`IAppStore` only** — `class IAppStore(ISegmentStore, ISettingsStore, ITrackEditor)`.
  **Not** into `ISegmentStore`: `tests/test_pipeline.py::MockStore` subclasses `ISegmentStore`
  and must not be forced to grow a method the pipeline never calls.
- FK order is mandatory — `playlist_tracks.file_hash` references `tracks` with **no**
  `ON DELETE CASCADE` (only `playlist_id` cascades), so `DELETE FROM tracks` first
  raises `IntegrityError` while any playlist holds the song.
- One `with self._conn:` block. Parameterized SQL only.

The confirmation's corrected-segment count comes from the existing
`Track.corrected_count` (already populated by `list_tracks`) — Lane A adds no count method.

### 3.2 Pipeline factory (Lane B, new file `analysis/factory.py`, Qt-free)

```python
def build_pipeline(
    config: AppConfig,
    store: ISegmentStore,
    *,
    verbose: bool = False,
) -> AnalysisPipeline:
    """Compose a production pipeline: AudioSeparator over config.stems_dir and
    config.model_dir, ThresholdClassifier over config.archetypes_path."""
```

Mirror `run_batch.main()`'s wiring exactly — `AudioSeparator(cache_dir=str(config.stems_dir),
model_dir=str(config.model_dir) if config.model_dir else None, verbose=verbose)` and
`ThresholdClassifier(calibration_path=config.archetypes_path)`. Never `NullSeparator`.

### 3.3 Add-songs options (Lane B, in `ui/dialogs/add_songs.py`)

```python
@dataclass(frozen=True)
class AddSongsOptions:
    paths: tuple[Path, ...]          # already expanded; folders resolved by the dialog
    reanalyse_existing: bool         # plan §2.5 — never overrides manual corrections
    target_playlist_id: int | None   # add each analysed track to this playlist
    pause_playback: bool             # decision D3
```

`AddSongsDialog.options() -> AddSongsOptions | None` — `None` when the user cancelled.
The dialog runs `preflight()` and refuses to enable OK while `report.blockers` is non-empty
or `report.usable_files` is empty; rejected files are listed with their `FileCheck.detail`.

Folder expansion is the dialog's job (a "recursive" checkbox), so `paths` reaching the
worker is always a flat tuple of real files. Reuse `SUPPORTED_SUFFIXES` from
`analysis/environment.py`.

### 3.4 Analysis worker (Lane B, `ui/analysis_worker.py`)

```python
class AnalysisWorker(QThread):
    progress  = Signal(int, int, str, str)  # file_index (1-based), file_total, filename, stage value
    fileDone  = Signal(object)              # AnalysisResult — persist on the MAIN thread
    fileFailed = Signal(str, str)           # source path, human message
    fileSkipped = Signal(str, str)          # source path, reason
    # QThread.finished (inherited) fires last, always, cancelled or not

    def __init__(self, pipeline: AnalysisPipeline, paths: Sequence[Path],
                 decisions: Mapping[str, bool], parent=None) -> None: ...

    def cancel(self) -> None:
        """Thread-safe. Honoured at stage and file boundaries only —
        a separation in flight runs to completion."""

    @property
    def cancelled(self) -> bool: ...
```

- `decisions` maps `str(path)` → analyse (True) / skip (False). The **main thread** builds
  it by calling `pipeline.precheck()` per file before the worker starts, because `precheck`
  reads SQLite. The worker never calls `precheck`, `persist`, or any store method.
- `run()` iterates paths; catches `AnalysisCancelled` and stops; catches every other
  `Exception` per file, emits `fileFailed`, and **continues to the next file** — one bad
  file must not abort a batch.
- The worker never touches Qt widgets, SQLite, or playback objects.

### 3.5 Progress dialog (Lane B, `ui/dialogs/analysis_progress.py`)

```python
class AnalysisProgressDialog(QDialog):
    cancelRequested = Signal()

    def set_progress(self, file_index: int, file_total: int, filename: str, stage: str) -> None: ...
    def add_result(self, filename: str, outcome: str, detail: str = "") -> None: ...  # "done"|"failed"|"skipped"
    def set_finished(self) -> None: ...   # Cancel becomes Close
```

Pressing Cancel emits `cancelRequested`, disables the button, and shows
**"Finishing current song…"** — never a frozen dialog (plan §2.3). `separating` must
carry the "this can take several minutes" note. Stage text comes from the `Stage` StrEnum
values, humanised.

### 3.6 D3 setting key

`"pause_playback_during_analysis"`, read/written through `ISettingsStore`, `"0"`/`"1"`,
**default `"0"`**. The dialog checkbox seeds from it and writes it back on accept.
Lane B owns reading/writing it in the dialog; the orchestrator owns acting on it.

## 4. Test rules

- **Never run real analysis or real separation.** A track is ~11 s without separation and
  minutes with it; the suite runs in ~40 s and stays there. Inject a stub pipeline whose
  `analyse()` returns a canned `AnalysisResult`.
- Qt tests start with `pytest.importorskip("pytestqt")` and use `qtbot.addWidget`, matching
  `tests/test_analysis_mode.py`.
- Run only your own test files: `python -m pytest tests/test_<yours>.py`. Other lanes are
  mid-edit; their failures are not yours.
- Lint only your own files: `python -m ruff check <your files>`.
- **Do not commit.** The orchestrator commits after the integration gate.

## 5. Gate 4 split

| Finding | Where | Owner |
|---|---|---|
| **H1** app refuses to start without loopMIDI | `MidoPort` fallback + `ui/app.py` no longer aborting | Lane C |
| **H1** persistent "MIDI disabled" banner | `main_window.py` | Orchestrator |
| **H2** only `NoSegmentsError` caught around `attach()` | `main_window.py::_on_load_decoded` | Orchestrator |
| **H3** correction CLI skips `ensure_calibration_copy` | `correction/cli.py` save path | Lane C |
| **H5** bare `except Exception` misreports playlist failures | `modes/home.py::_on_new_playlist` | Orchestrator |

Lane C's H1 half: `Application` must construct successfully when the MIDI port is absent,
falling back to a null port, and expose that fact — freeze it as:

```python
class Application:
    midi_available: bool      # False when the requested port was not found
    midi_error: str | None    # the MidiPortNotFoundError message, for the banner
```

`ui/app.py` stops returning exit code 2 on `MidiPortNotFoundError`. Use the existing
`MockMidiPort` as the null port only if it is genuinely inert; otherwise add a
`NullMidiPort` to `guitar_helper/midi/` (Lane C owns that new file if needed).

## 6. Out of scope for Wave 2

Gate 5 packaging entirely (pyproject, PyInstaller, `%LOCALAPPDATA%`, weight bundling) —
it needs a clean-machine test and a frozen-build size measurement on the user's hardware.
The `TODO.md` "Small carry-overs" list. Everything under the plan's §10.

Live rig verification (plan §5 step 8) is the user's, not an agent's: multi-song add,
remove-and-re-add, cancel mid-separation then a clean re-run, and playback-during-analysis
checked by ear.
