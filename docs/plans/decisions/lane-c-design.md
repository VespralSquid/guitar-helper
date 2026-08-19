# Lane C design decisions — ISSUE-007 (Gate 2 §4.1 + Gate 3 steps 1–3, Qt-free)

**Scope:** `analysis/pipeline.py`, `analysis/environment.py` (new), `run_analysis.py`,
`run_batch.py`, `tests/test_pipeline.py`, `tests/test_environment.py` (new),
`tests/test_run_batch.py`, `tests/test_ui_smoke.py:371`.
**Out of scope, by charter:** everything under `guitar_helper/ui/`. This note designs the
seams Wave 2 attaches to and nothing else.

**No frozen contract needs to change.** `run(path, title, artist, k, discard_corrections)`
keeps its exact signature and semantics; `ISourceSeparator.separate_guitar` and
`ISegmentStore.save_track` / `save_segments` are called unchanged. Four items cross a lane
boundary and are handled under §7 of the orchestration doc — see *Cross-lane items* at the end.

**Verified during design** (venv, no analysis or separation run):

| Probe | Result |
|---|---|
| `shutil.which("ffmpeg")` | `C:\msys64\ucrt64\bin\ffmpeg.EXE` — **present**. `CLAUDE.md` is stale. `ffprobe` also present. |
| `find_spec` for `audio_separator`, `torch`, `onnxruntime`, `diffq`, `pydub`, `soundfile`, `librosa`, `yaml` | all present, **3.22 ms total** |
| `import audio_separator.separator` | **2.77 s**, and it loads `torch` into the process permanently |
| `inspect.signature(Separator.__init__)` | no progress, cancel, callback or abort parameter. `model_file_dir` defaults to `'/tmp/audio-separator-models/'` |
| `Path('/tmp/audio-separator-models')` on Windows | resolves to `C:\tmp\audio-separator-models`; contains `htdemucs_6s.yaml` (21 B), `5c90dfd2-34c22ccb.th` (52.4 MB), `download_checks.json` |
| `segments.file_hash REFERENCES tracks(file_hash)` (`db/schema.py:29`) | `save_track` **must** precede `save_segments` |
| ruff config | `select = ["E","F","W","I"]`; CI lints `guitar_helper/` only, not `tests/` |
| `requirements-separation.txt` | `audio-separator` is installed `--no-deps` with a hand-written `diffq` stub — so `find_spec("audio_separator")` succeeding does **not** prove the stack imports |

---

## Decisions

### D1. What `AnalysisResult` carries, and where it lives

**Options:** (a) the plan's five fields (`file_hash`, `duration_ms`, `title`, `artist`,
`segments`) with `persist(result, source_path)` taking the path separately; (b) a
self-contained result carrying everything `save_track` needs, with `persist(result)`;
(c) a result plus a back-reference to the pipeline.

**Decision:** (b). `AnalysisResult` carries `file_hash`, `duration_ms`, `title`, `artist`,
`filename`, `source_path`, `stem_path`, `segments`. It lives in `analysis/pipeline.py`.
`persist()` takes no path.

**Why:** `save_track` needs `filename` and a *resolved* `source_path`, and `Path.resolve()`
hits the filesystem on Windows — so it must happen on the worker side, in `analyse()`, not in
`persist()`. Option (a) forces `persist()` to recompute `Path(source_path).name`, which works
but leaves the door open to a caller passing a different path than the one analysed; making
the result self-contained makes that impossible. `pipeline.py` rather than a new
`analysis/interfaces.py` because Wave 2's worker imports the pipeline anyway, and a new module
buys nothing but an import.

**Consequence for the implementer:** `persist(result, ...)` — one positional argument. Every
value `save_track` and `save_segments` need is a plain field of `result`; `persist()` must
contain no `Path` construction, no `os.*` call and no file open.

### D2. Progress callback signature — `Callable[[Stage], None]`, not the plan's 3-arg form

**Options:** (a) the plan's `Callable[[Stage, int, int], None]` (stage, file_index,
file_total); (b) `Callable[[Stage], None]` with *n of m* composed by the caller;
(c) `Callable[[Stage, int, int], None]` where the ints are stage index and stage count.

**Decision:** (b).

**Why:** `analyse()` analyses one file. It does not know its index in a batch and cannot be
given one without every single-file caller inventing `(1, 1)`. The composition the plan wants
is one closure in Wave 2's worker — `lambda stage: self.progress.emit(str(stage), idx, total)`
— and putting it there keeps the pipeline honest about what it knows. (c) is rejected because
stage durations differ by four orders of magnitude (separating = minutes, classifying =
0.00 s measured), so a stage-index bar would be a fake percentage, which §2.2 explicitly
rules out.

**Consequence for the implementer:** the pipeline never sees a file index. *n of m* is not
implemented in Wave 1 at all — it is a Wave 2 closure over the stage callback.

### D3. `Stage` is a `StrEnum`, not a `Literal`

**Options:** (a) `Literal["hashing", ...]`; (b) `enum.StrEnum`.

**Decision:** (b), with a module-level `ANALYSE_STAGES` tuple giving the emission order.

**Why:** `StrEnum` members compare equal to their strings and pass through a Qt
`Signal(str)` unchanged, so (b) costs nothing that (a) gives. It buys two things: a typo
becomes an `AttributeError` instead of a silently-never-matching string, and tests can assert
`recorded == list(ANALYSE_STAGES)` without duplicating the stage names in the test file.
Verified on 3.14.3: `Stage.HASHING == "hashing"` is `True`.

**Consequence for the implementer:** define `Stage` and `ANALYSE_STAGES` in `pipeline.py`;
`ANALYSE_STAGES` excludes `SAVING`, which only `persist()` emits.

### D4. Progress is emitted *before* the work of the stage, and cancel is polled before that

**Decision:** one private helper drives every stage boundary:

```python
def _enter(self, stage: Stage, path: Path, progress, should_cancel) -> None:
    if should_cancel is not None and should_cancel():
        raise AnalysisCancelled(path, stage)
    if progress is not None:
        progress(stage)
```

**Why:** "now doing X" is the only honest reading of a stage-level signal — the alternative
("finished X") leaves the UI showing nothing for the minutes that `separating` runs, which is
exactly the stage that looks hung. Checking cancel *before* announcing the stage prevents the
UI from displaying "Separating…" one frame before the run aborts.

**Consequence for the implementer:** six `_enter()` calls in `analyse()`, in the order of
`ANALYSE_STAGES`. Completion of the last stage is signalled by `analyse()` returning, not by a
seventh callback.

### D5. Cancellation raises; it does not return `None`

**Options:** (a) `analyse()` returns `AnalysisResult | None`; (b) raises
`AnalysisCancelled`; (c) returns a result with a `cancelled` flag.

**Decision:** (b). `class AnalysisCancelled(Exception)` with `path: Path` and `stage: Stage`
(the stage that was about to start).

**Why:** (a) and (c) both let a caller that forgets the check persist a garbage or empty
result. An exception cannot be ignored. `run()` never passes `should_cancel`, so the CLIs can
never see it.

**Consequence for the implementer:** `AnalysisCancelled` lives in `pipeline.py` beside
`ManualCorrectionsExistError`. It subclasses `Exception` directly — note that `run_batch`'s
broad `except Exception` would print it as FAILED, which is harmless because `run()` cannot
raise it.

### D6. What cancellation actually promises

**Decision — state this verbatim in the `analyse()` docstring:**

1. `should_cancel()` is polled at **stage boundaries only** — six points, before `hashing`,
   `separating`, `decoding`, `features`, `segmenting`, `classifying`.
2. If it returns `True`, `analyse()` raises `AnalysisCancelled` and does no further work.
   `persist()` is never reached, so **nothing is written to the database** for that file.
3. **A separation already in flight runs to completion.** Verified: `Separator.separate()`
   exposes no abort. If Cancel is pressed one second into a separation, the cancel is observed
   only when `separate_guitar()` returns — worst case, minutes. The UI must say
   "Finishing current song…"; it must not claim the operation stopped.
4. The completed stem **is kept** in the cache. That is deliberate: it is the expensive
   artifact, and Lane B's atomic publish makes a published stem valid by construction.
5. Cancel is **not** polled after `classifying`. A result that finished computing is returned
   and should still be persisted — cancellation prevents *starting* new work, it does not
   discard finished work. Wave 2 must persist a result it already holds.
6. Lane C makes **no promise about a killed process**. Partial-file protection there is Lane
   B's atomic publish, not this design.

**Consequence for the implementer:** between-file cancellation is not implemented in Wave 1 —
there is no batch driver in Lane C's files (`run_batch` is not being given cancellation). The
per-stage poll is what makes a Wave 2 loop possible.

### D7. The corrections guard sits on the main-thread side, in two places

**Options:** (a) leave it inline in `run()` only; (b) extract a `precheck()` the GUI can call
up front; (c) (b) plus a re-check inside `persist()`.

**Decision:** (c).

**Why:** (a) forces the GUI either to spend minutes separating a track it will refuse, or to
reimplement `any(s.manually_corrected for s in store.get_segments(h))` — a second source of
truth for the invariant, which §2.1 warns against. (b) alone leaves a real TOCTOU window: the
user can correct segments in Analysis mode while a three-minute separation runs, and the
result would then overwrite them. `persist()` is the only writer and runs on the main thread
with the store, so the re-check costs one `get_segments` call at the last point before
irreversible loss — the loss this project has already suffered once.

**Consequence for the implementer:** `precheck()` hashes with `AudioLoader.hash_file` — **not**
`AudioLoader.load`, which decodes the whole file through pydub for `.m4a`/`.mp3` duration.
`persist()` gains `discard_corrections: bool = False` and raises `ManualCorrectionsExistError`
before writing anything when corrections exist and the flag is false. `run()` passes its own
flag through to both.

### D8. `TrackPrecheck` reports segment counts, not an `in_library` query

**Options:** (a) `in_library` from `list_tracks()`; (b) derive it from `get_segments()`.

**Decision:** (b). `in_library` is a property: `segment_count > 0`.

**Why:** `list_tracks()` is the only track-listing method and is O(library); calling it per
file during an add makes preflight O(n·m). More importantly, treating a track row with zero
segments as *not* in the library is the correct behaviour — that state is exactly the
partial-persist wreckage described in *What this does not cover*, and the right response to it
is to re-analyse.

**Consequence for the implementer:** one `get_segments(file_hash)` call per precheck. The
`in_library` property must be documented as "has stored segments", not "has a track row".

### D9. `separator` becomes required **and keyword-only**

**Options:** (a) second positional parameter; (b) keyword-only after `*`.

**Decision:** (b) — `def __init__(self, store, *, separator, classifier=None, verbose=False,
use_hpss=False)`.

**Why:** every existing call site already passes `classifier=`, `separator=`, `verbose=` and
`use_hpss=` as keywords (verified across `run_analysis.py`, `run_batch.py`,
`tests/test_pipeline.py`), so keyword-only costs zero churn beyond adding the argument, and it
removes the positional ambiguity between `classifier` and `separator` that the current order
invites. Omitting it is a `TypeError` — the Gate 2 exit criterion.

**Consequence for the implementer:** delete `from .source_separator import NullSeparator` from
`pipeline.py` — it becomes unused and ruff `F401` will fail the lane otherwise. Nine call
sites change: `tests/test_pipeline.py` lines **65, 75, 84, 91, 99, 118, 131** (bare), line
**153** (already passes `separator=`, unchanged), and `tests/test_ui_smoke.py:371`.

### D10. `store` stays a required constructor parameter even though `analyse()` never uses it

**Options:** (a) make `store` optional so a worker can build an analyse-only pipeline;
(b) keep it required and have the worker hold the main thread's pipeline instance.

**Decision:** (b).

**Why:** this is precisely the D14 shape — `LoadWorker` holds the whole `Application` and only
calls `decode()`. An optional store reintroduces the trap in the other direction: a
worker-built pipeline whose `persist()` fails with `AttributeError: 'NoneType'`. Keeping it
required means one pipeline instance, constructed on the main thread, whose *methods* carry
the thread contract.

**Consequence for the implementer:** "analyse touches no store" is proven by a test double, not
by the type system — see the Verification checklist item 3.

### D11. `--no-separate` is **removed**, not renamed

**Options:** (a) remove from both CLIs; (b) rename `--debug-full-mix` with a printed warning.

**Decision:** (a) — removed from `run_analysis.py` and `run_batch.py`. Both CLIs construct
`AudioSeparator` unconditionally.

**Why:** the pre-decision that `NullSeparator` is *test-only* and a shipped CLI flag that
constructs it cannot both be true. (b) would leave an exception to police forever, and the
flag name appears in project docs from which people copy commands. The cost is real and small:
re-running the stem-vs-mix A/B now takes a three-line throwaway script instead of a flag, and
that experiment is already done and recorded in ISSUE-007.

**Consequence for the implementer:** in both CLIs delete the `--no-separate` argument, the
`if args.no_separate:` print, the conditional separator expression and the `NullSeparator`
import. `AudioSeparator(...)` construction is cheap and lazy — it only stores paths — so
constructing it in a test that patches `AnalysisPipeline` is harmless.

### D12. The preflight return type: two severities, two check kinds, one report

**Options:** (a) a bool + list of strings; (b) a dict keyed by check name; (c) dataclasses
with an explicit `Severity` and a separate per-file result type.

**Decision:** (c). `Severity` (BLOCK/WARN), `Check` (environment-wide), `FileCheck`
(per-file), `EnvironmentReport` (both, with derived properties).

**Why:** the caller's central question is "may I start at all?", and that is a different
question from "which of these 40 files will work". A flat list cannot express it; a dict makes
tests assert on string keys and gives the UI no severity to render. The two-type split maps
directly onto the two dialogs Wave 2 builds — a blocking message box, and a per-file exclusion
list. Two severities are enough; a third (INFO) would have no consumer.

**Consequence for the implementer:** `can_proceed` is `not blockers and bool(usable_files)` —
a preflight with zero usable files must not let the run start even when the environment is
perfect. Every `Check.name` and `FileCheck.code` is a stable identifier that tests assert on;
`detail` and `remedy` are human strings that tests must not assert on.

### D13. The separation-stack check is `find_spec` over a **module list**, with an opt-in deep check

**Options:** (a) the plan's single `find_spec("audio_separator")`; (b) a real import;
(c) `find_spec` over the stack's fragile members, plus an opt-in real import.

**Decision:** (c). Shallow check: `find_spec` for `audio_separator`, `torch`, `onnxruntime`,
`diffq` — 3.22 ms measured for eight modules. Deep check (`deep=True`, default off): a real
`import audio_separator.separator` in a `try`.

**Why:** (a) is necessary but not sufficient, and this project is the proof —
`requirements-separation.txt` installs `audio-separator` with `--no-deps` and a hand-written
`diffq` stub, so the package is importable-looking while its import chain can be broken. This
is the same shape as ISSUE-008's "a duration check alone is necessary but not sufficient".
(b) alone is unaffordable: measured 2.77 s and it loads torch into the process permanently, so
it can never run on the Qt main thread. (c) catches the common cases for free and leaves the
authoritative check available where its cost is already being paid.

**Consequence for the implementer:** `check_separation_stack(deep=False)`. Its docstring must
say `deep=True` costs ~2.8 s and permanently loads torch, and must never be called on the Qt
main thread. Wave 2 may call it once, on the worker, before the first separation; if it fails
there the file fails with the `ImportError` message. **The shallow check is not a guarantee**
— say so in `EnvironmentReport`'s docstring.

### D14. The weights check is presence-only, and its failure **blocks**

**Options:** (a) hardcode `5c90dfd2-34c22ccb.th`; (b) parse `htdemucs_6s.yaml` for model ids;
(c) require `htdemucs_6s.yaml` plus at least one `*.th` above a size floor.

**Decision:** (c), severity BLOCK. `_MODEL_CONFIG = "htdemucs_6s.yaml"`,
`_MIN_WEIGHTS_BYTES = 10 * 1024 * 1024`.

**Why:** (a) pins a third-party content hash that Lane C does not control. (b) needs `pyyaml`,
which is a *separation-stack* dependency — the check must work on machines where the stack is
absent, which is the case it exists for. (c) needs nothing but `pathlib` and catches every
realistic case (empty dir, missing download, wiped temp dir). BLOCK rather than WARN because
the plan already settled that weights are bundled, so absence means a damaged install, and a
silent 52 MB download contradicts the "fully offline" claim. Verified layout:
`htdemucs_6s.yaml` (21 B) + `5c90dfd2-34c22ccb.th` (52.4 MB).

**Consequence for the implementer:** the remedy string must be actionable for the dev case
too — the weights currently live in `C:\tmp\audio-separator-models`, which cleanup tools
delete. Word it as "reinstall, or run one separation from the CLI with a network connection to
re-fetch them." When `model_dir` is `None`, resolve it to the module constant
`_DEFAULT_MODEL_DIR = Path("/tmp/audio-separator-models")`, which mirrors
`Separator.__init__`'s default and resolves to `C:\tmp\audio-separator-models` on Windows —
verified. Do **not** import `Separator` to read the default; that is the 2.77 s import.

### D15. ffmpeg is a WARN that becomes a per-file rejection

**Decision:** missing ffmpeg is `Severity.WARN` on the environment check, and it is fed into
the per-file pass so that `.mp3` / `.m4a` / `.aac` files are rejected with
`code="needs_ffmpeg"` while `.wav` / `.flac` / `.ogg` / `.aiff` pass.

**Why:** a warning the user cannot act on per-file is noise. Turning it into a named exclusion
reason answers the only question that matters — "which of my files won't work, and why". This
is why `preflight()` exists as a single entry point rather than two independent functions: it
is the thing that wires one result into the other.

**Consequence for the implementer:** `check_files(paths, *, ffmpeg_available: bool)`. Note
this checks ffmpeg's *presence on PATH*, not its ability to decode a given container — an
ffmpeg build lacking an AAC decoder still passes.

### D16. The audio-suffix sets are imported from `audio_loader`, not redeclared

**Options:** (a) declare a fresh suffix set in `environment.py`; (b) import
`audio_loader._SUPPORTED_NATIVE` / `_SUPPORTED_PYDUB`.

**Decision:** (b), and `run_batch._AUDIO_EXTENSIONS` becomes an alias for the derived
`SUPPORTED_SUFFIXES`.

**Why:** there are already two copies (`audio_loader` and `run_batch._AUDIO_EXTENSIONS`) and
(a) would make three. The failure mode of divergence is concrete: preflight accepts a file that
`AudioLoader.load` then rejects with `ValueError`, mid-run, after the dialog said it was fine.
`audio_loader.py` is owned by no lane in this wave, so making the names public is not Lane C's
edit to make — importing the private names within the same package is the lesser evil and
removes one of the three copies now. No new import cost: `application.py` already imports
`AudioLoader`.

**Consequence for the implementer:** `environment.py` exports
`SUPPORTED_SUFFIXES = _SUPPORTED_NATIVE | _SUPPORTED_PYDUB` and
`FFMPEG_SUFFIXES = _SUPPORTED_PYDUB`. Keep the name `_AUDIO_EXTENSIONS` alive in `run_batch`
(re-bound to `SUPPORTED_SUFFIXES`) because `tests/test_run_batch.py:10` imports it. File a
finding that `audio_loader` should export these publicly in Wave 2.

### D17. Writability is tested by an actual write, and only on directories

**Decision:** create the directory with `mkdir(parents=True, exist_ok=True)`, then write and
delete a `tempfile.NamedTemporaryFile(dir=...)`. Applied to `stems_dir` and to
`db_path.parent`. Both BLOCK on failure. An *existing* `library.db` file is not probed.

**Why:** `os.access` is unreliable on Windows for exactly this question. Creating the stem
directory is correct behaviour on a fresh install, not a side effect to avoid. Probing an
existing SQLite file for writability means opening it, which risks touching a database another
component owns — not worth it for a case (read-only `library.db` inside a writable directory)
that surfaces immediately as a clear sqlite error at `persist()`.

**Consequence for the implementer:** put the probe in one private helper
`_is_writable(directory) -> tuple[bool, str]` so tests can monkeypatch **it** rather than
trying to make a directory read-only — `chmod` on a directory does not restrict writes on
Windows and a test that relies on it will pass for the wrong reason.

---

## Signatures / schema

### `guitar_helper/analysis/pipeline.py`

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from guitar_helper.db.interfaces import ISegmentStore, Segment

from .audio_loader import AudioLoader
from .feature_extractor import FeatureExtractor
from .segmenter import Segmenter
from .source_separator import ISourceSeparator
from .tone_classifier import BaseToneClassifier, ThresholdClassifier

_HOP = 512


class Stage(StrEnum):
    HASHING = "hashing"
    SEPARATING = "separating"
    DECODING = "decoding"
    FEATURES = "features"
    SEGMENTING = "segmenting"
    CLASSIFYING = "classifying"
    SAVING = "saving"


ANALYSE_STAGES: tuple[Stage, ...] = (
    Stage.HASHING, Stage.SEPARATING, Stage.DECODING,
    Stage.FEATURES, Stage.SEGMENTING, Stage.CLASSIFYING,
)

ProgressFn = Callable[[Stage], None]
CancelFn = Callable[[], bool]


class ManualCorrectionsExistError(Exception):
    """Unchanged — same message and same .file_hash attribute."""


class AnalysisCancelled(Exception):
    """Raised by analyse() when should_cancel() returns True at a stage boundary.

    Nothing has been persisted. A separation already in flight ran to completion;
    its stem is kept in the cache.
    """

    def __init__(self, path: Path, stage: Stage) -> None:
        self.path = Path(path)
        self.stage = stage
        super().__init__(f"Analysis of {self.path.name} cancelled before {stage}")


@dataclass(frozen=True)
class AnalysisResult:
    """Everything persist() needs. Carries no open handle and no callable, so it
    crosses a thread boundary intact. frozen is shallow: do not mutate .segments."""
    file_hash: str
    duration_ms: int
    filename: str          # Path(source).name
    source_path: str       # str(Path(source).resolve()) — resolved on the worker
    stem_path: str         # what the separator returned; evidence the invariant held
    title: str | None
    artist: str | None
    segments: list[Segment]   # id=0, not yet persisted


@dataclass(frozen=True)
class TrackPrecheck:
    """Facts about a track already in the library. Never raises."""
    file_hash: str
    segment_count: int
    corrected_count: int

    @property
    def in_library(self) -> bool:
        """True when the track has stored segments. A track row with zero
        segments (partial persist) reads as False, so it is re-analysed."""
        return self.segment_count > 0


class AnalysisPipeline:

    def __init__(
        self,
        store: ISegmentStore,
        *,
        separator: ISourceSeparator,
        classifier: BaseToneClassifier | None = None,
        verbose: bool = False,
        use_hpss: bool = False,
    ) -> None: ...

    # --- main thread only (reads the store) ---------------------------------

    def precheck(self, path: str | Path) -> TrackPrecheck:
        """Hash the file (hash_file only — never load(), which decodes m4a) and
        report what the library already holds for it. ~0.05 s per 44 MB."""

    # --- worker safe (touches no store, no Qt) ------------------------------

    def analyse(
        self,
        path: str | Path,
        *,
        title: str | None = None,
        artist: str | None = None,
        k: int | None = None,
        progress: ProgressFn | None = None,
        should_cancel: CancelFn | None = None,
    ) -> AnalysisResult:
        """Pure computation. Touches no store, no Qt, no playback object.
        Raises AnalysisCancelled at a stage boundary; other failures propagate
        unwrapped."""

    # --- main thread only (the sole writer) ---------------------------------

    def persist(
        self,
        result: AnalysisResult,
        *,
        discard_corrections: bool = False,
        progress: ProgressFn | None = None,
    ) -> None:
        """save_track then save_segments (FK order is mandatory). Re-runs the
        corrections guard first: a user may have corrected segments while the
        separation ran."""

    # --- unchanged public contract ------------------------------------------

    def run(
        self,
        path: str | Path,
        title: str | None = None,
        artist: str | None = None,
        k: int | None = None,
        discard_corrections: bool = False,
    ) -> list[Segment]:
        """precheck -> guard -> analyse -> persist. Signature and behaviour
        identical to before the split."""
```

`run()`'s body, exactly:

```python
path = Path(path)
pre = self.precheck(path)
_guard_corrections(pre, discard_corrections)      # raises before any separation
result = self.analyse(path, title=title, artist=artist, k=k)
self.persist(result, discard_corrections=discard_corrections)
return result.segments
```

`_guard_corrections` is a module-level private used by `run()` and `persist()`:

```python
def _guard_corrections(pre: TrackPrecheck, discard_corrections: bool) -> None:
    if not discard_corrections and pre.corrected_count:
        raise ManualCorrectionsExistError(pre.file_hash)
```

`persist()`'s body, exactly:

```python
pre = self._inspect(result.file_hash)             # private: store read, no file I/O
_guard_corrections(pre, discard_corrections)
if progress is not None:
    progress(Stage.SAVING)
self._store.save_track(
    result.file_hash, result.filename, result.title,
    result.artist, result.duration_ms, result.source_path,
)
self._store.save_segments(result.file_hash, result.segments)
```

Stage-to-work mapping inside `analyse()` — the implementer changes no computation, only
inserts the six `_enter()` calls:

| Stage | Work that follows |
|---|---|
| `HASHING` | `self._loader.load(path)` → `(duration_ms, file_hash)` |
| `SEPARATING` | `self._separator.separate_guitar(path, file_hash)` |
| `DECODING` | `self._loader.load_mono(stem_path)` |
| `FEATURES` | `extract()` + `extract_for_classification()` |
| `SEGMENTING` | `find_boundaries(...)` |
| `CLASSIFYING` | the per-boundary loop building `Segment`s |

`source_path` / `filename` are computed in `analyse()` from `path` before the result is built.

### `guitar_helper/analysis/environment.py` (new)

```python
from __future__ import annotations

import importlib.util
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .audio_loader import _SUPPORTED_NATIVE, _SUPPORTED_PYDUB

SUPPORTED_SUFFIXES: frozenset[str] = frozenset(_SUPPORTED_NATIVE | _SUPPORTED_PYDUB)
FFMPEG_SUFFIXES: frozenset[str] = frozenset(_SUPPORTED_PYDUB)

_SEPARATION_MODULES = ("audio_separator", "torch", "onnxruntime", "diffq")
_DEFAULT_MODEL_DIR = Path("/tmp/audio-separator-models")   # mirrors Separator's default
_MODEL_CONFIG = "htdemucs_6s.yaml"
_MIN_WEIGHTS_BYTES = 10 * 1024 * 1024


class Severity(StrEnum):
    BLOCK = "block"   # ingestion cannot start
    WARN = "warn"     # ingestion can start; some formats or files will fail


@dataclass(frozen=True)
class Check:
    name: str          # stable id — tests assert on this
    ok: bool
    severity: Severity # the severity this check carries WHEN it fails
    detail: str        # what was found; for humans
    remedy: str        # what to do; for humans


@dataclass(frozen=True)
class FileCheck:
    path: Path
    code: str          # "ok" | "missing" | "unsupported_suffix" | "needs_ffmpeg" | "unreadable"
    detail: str

    @property
    def ok(self) -> bool:
        return self.code == "ok"


@dataclass(frozen=True)
class EnvironmentReport:
    """Preflight verdict. The separation-stack check is find_spec-based and is
    necessary but NOT sufficient — a --no-deps install can satisfy it and still
    fail to import. Use check_separation_stack(deep=True) off the main thread
    for the authoritative answer."""
    checks: list[Check]
    files: list[FileCheck]

    @property
    def blockers(self) -> list[Check]: ...
    @property
    def warnings(self) -> list[Check]: ...
    @property
    def usable_files(self) -> list[Path]: ...
    @property
    def rejected_files(self) -> list[FileCheck]: ...
    @property
    def can_proceed(self) -> bool:
        return not self.blockers and bool(self.usable_files)


def check_separation_stack(*, deep: bool = False) -> Check: ...
def check_model_weights(model_dir: str | Path | None = None) -> Check: ...
def check_ffmpeg() -> Check: ...
def check_writable(directory: str | Path, *, name: str) -> Check: ...
def check_files(paths: Sequence[str | Path], *, ffmpeg_available: bool) -> list[FileCheck]: ...

def preflight(
    paths: Sequence[str | Path] = (),
    *,
    stems_dir: str | Path,
    db_path: str | Path,
    model_dir: str | Path | None = None,
) -> EnvironmentReport:
    """Run every check and wire the ffmpeg result into the per-file pass."""
```

Check names and severities, fixed:

| `Check.name` | Method | Severity when failing |
|---|---|---|
| `separation_stack` | `find_spec` over `_SEPARATION_MODULES` (3.2 ms) | **BLOCK** |
| `model_weights` | `<model_dir>/htdemucs_6s.yaml` is a file **and** some `*.th` ≥ 10 MB | **BLOCK** |
| `ffmpeg` | `shutil.which("ffmpeg")` | WARN |
| `stem_cache_writable` | mkdir + temp write in `stems_dir` | **BLOCK** |
| `database_writable` | mkdir + temp write in `Path(db_path).parent` | **BLOCK** |

`FileCheck.code` decision order: `missing` (not `is_file()`) → `unsupported_suffix` (suffix not
in `SUPPORTED_SUFFIXES`) → `needs_ffmpeg` (suffix in `FFMPEG_SUFFIXES` and not
`ffmpeg_available`) → `unreadable` (`open(path, "rb").read(1)` raises `OSError`) → `ok`.
**Preflight never hashes** — see *What this does not cover*.

### CLI changes

`run_analysis.py`: drop `--no-separate`, the `if args.no_separate:` print, the conditional and
the `NullSeparator` import. `separator = AudioSeparator(cache_dir=str(cfg.stems_dir),
model_dir=model_dir, verbose=args.verbose)` unconditionally. `AnalysisPipeline(store,
separator=separator, classifier=classifier, verbose=..., use_hpss=...)`.

`run_batch.py`: identical treatment, plus `_AUDIO_EXTENSIONS = SUPPORTED_SUFFIXES` imported
from `environment.py`.

---

## Sequencing

1. **D9 + D11 — the required separator and the CLI flag removal.** Do this first and alone.
   It churns nine call sites and both CLIs, it is the widest edit in the lane, and everything
   after it builds on the new constructor. Run `pytest tests/test_pipeline.py
   tests/test_run_batch.py tests/test_ui_smoke.py` and get green before touching anything else
   — at this point behaviour is unchanged, so any failure is a mechanical mistake and is cheap
   to find.
2. **D1 + D7 + D8 — the `precheck` / `analyse` / `persist` split, `run()` rewired.** No
   progress, no cancel, no new parameters beyond `discard_corrections` on `persist`. The seven
   existing behavioural tests must pass **without modification** other than step 1's
   constructor argument; that is the regression proof for the split.
3. **D2–D6 — `Stage`, `ANALYSE_STAGES`, `_enter()`, `AnalysisCancelled`.** Purely additive:
   both new parameters default to `None` and `run()` passes neither, so step 2's tests cannot
   move.
4. **D12–D17 — `environment.py` + `tests/test_environment.py`.** Fully independent of 1–3;
   done last so the critical path Wave 2 blocks on (`analyse`/`persist`) is finished first, and
   so the suffix-sharing decision is settled against the final `run_batch`.
5. `ruff check guitar_helper/analysis/pipeline.py guitar_helper/analysis/environment.py
   guitar_helper/run_analysis.py guitar_helper/run_batch.py` — lane-scoped only, per §7.

---

## What this does not cover

- **`save_track` + `save_segments` are not one transaction.** Lane A makes each atomic
  internally, but a `save_segments` failure after a successful `save_track` leaves a track row
  with zero segments. Combining them needs a new store method in Lane A's file, and the FK
  (`segments.file_hash REFERENCES tracks(file_hash)`) forbids the reverse order. The only
  mitigation here is D8: such a track reads as `in_library == False` and is re-analysed on the
  next add. Full fix is Wave 2, alongside `delete_track`.
- **Nothing detects `NullSeparator` in production.** D9 makes the omission a `TypeError`;
  it does not stop someone writing `separator=NullSeparator()` in real code. The docstring and
  review are the whole defence. `AnalysisResult.stem_path` makes it *visible* after the fact,
  which is not the same as preventing it.
- **Cancellation cannot interrupt a separation.** Worst-case wait after Cancel is one full
  separation — minutes. Verified against the installed `Separator` API. Nothing in this design
  or in Wave 2 can change that without replacing the separation library.
- **The weights check is presence, not integrity.** A truncated or wrong `.th` above 10 MB
  passes. Hashing 52 MB belongs in packaging verification, not a preflight the user waits on.
- **The shallow stack check can pass on a stack that does not import.** This is not
  hypothetical here — see `requirements-separation.txt`. `deep=True` is the answer and Wave 2
  must call it once on the worker.
- **An existing read-only `library.db` inside a writable directory is not caught.** It
  surfaces as a sqlite error at `persist()`.
- **Preflight does not hash, so it cannot report duplicates.** Duplicate/corrections
  classification is per-file via `precheck()`, on the main thread, at 0.05 s per 44 MB. A
  200-file add would stall the UI ~10 s if Wave 2 prechecks everything up front. Wave 2's call:
  precheck lazily, one file at a time, or move hashing to the worker.
- **No batch driver.** *n of m* and between-file cancellation are Wave 2 closures over the
  stage callback. `run_batch` gains neither progress nor cancellation in this wave.
- **The UI cannot tell a cache hit from a fresh separation.** `separate_guitar` decides
  internally and the frozen contract exposes no `is_cached()`; Lane B is rewriting those
  internals. `Stage.SEPARATING` is always emitted, so Wave 2 must always show the
  "may take several minutes" copy, even when the stage returns in 5 ms.
- **ffmpeg presence ≠ ffmpeg capability.** A build without an AAC decoder passes the check and
  fails at decode.
- **Tag reading (`run_batch._read_tags`) is not promoted.** Wave 2's add-songs path will want
  it; moving it now would create an unowned module for no Wave 1 benefit.

---

## Verification checklist

Every assertion below must appear in the implementer's tests. **No test may invoke a real
separator or analyse anything longer than the 3 s `make_wav` fixture.**

### `tests/test_pipeline.py`

1. `AnalysisPipeline(store)` raises `TypeError`; `AnalysisPipeline(store, NullSeparator())`
   also raises `TypeError` (keyword-only); `AnalysisPipeline(store,
   separator=NullSeparator())` constructs. *(D9, Gate 2 exit criterion)*
2. The seven existing behavioural tests still pass with only the constructor argument added:
   first segment starts at 0, last ends at `duration_ms`, segments contiguous, all labels in
   `TONE_LABELS`, `run()` stores one track and ≥1 segment.
3. **`analyse()` touches no store.** Define a local `_ExplodingStore(ISegmentStore)` whose
   *every* method raises `AssertionError("analyse touched the store")`; assert
   `AnalysisPipeline(_ExplodingStore(), separator=NullSeparator()).analyse(wav)` returns an
   `AnalysisResult`. Do not settle for mock call-count assertions. *(D10 — this is the whole
   point of the lane)*
4. `persist(result)` calls `save_track` **before** `save_segments`, and the `save_track`
   argument tuple equals `(result.file_hash, result.filename, result.title, result.artist,
   result.duration_ms, result.source_path)` exactly. *(D1, FK order)*
5. `AnalysisResult` is frozen: assigning `result.file_hash` raises
   `dataclasses.FrozenInstanceError`. `result.filename == Path(wav).name`,
   `Path(result.source_path).is_absolute()`, and `result.stem_path == str(stem)` when a
   `RecordingSeparator` returns a distinct stem file.
6. `run()` returns exactly `result.segments` and leaves the store holding the same list.
7. `run()` on a corrected track raises `ManualCorrectionsExistError` **and the separator is
   never called** — assert `RecordingSeparator.calls == []`. *(D7 — this is the "do not spend
   minutes on a refused track" guarantee)*
8. `precheck()` does not decode: monkeypatch `AudioLoader.load` to raise, and assert
   `precheck()` still returns a `TrackPrecheck`. *(D7 — the m4a trap)*
9. `precheck()` on an unknown file → `segment_count == 0`, `corrected_count == 0`,
   `in_library is False`. On a track with one corrected segment → `corrected_count == 1`,
   `in_library is True`. On a track whose segments were deleted but whose row remains →
   `in_library is False`. *(D8)*
10. **TOCTOU:** build a result via `analyse()`, *then* seed a manually-corrected segment into
    the store, then `persist(result)` → raises `ManualCorrectionsExistError` and the store's
    segments are unchanged. `persist(result, discard_corrections=True)` overwrites. *(D7)*
11. **Progress order:** a recorder passed to `analyse()` receives exactly
    `list(ANALYSE_STAGES)`, in order, no repeats. A recorder passed to `persist()` receives
    exactly `[Stage.SAVING]`. `run()` emits nothing (it passes no callback). *(D2, D3, D4)*
12. **Cancel before `separating`:** a `should_cancel` returning `True` on its 2nd call makes
    `analyse()` raise `AnalysisCancelled` with `exc.stage == Stage.SEPARATING`; the
    `RecordingSeparator` was never called; the store is untouched. *(D5, D6)*
13. **Cancel is checked before the stage is announced:** in the case above, the progress
    recorder ends at `Stage.HASHING` — `Stage.SEPARATING` was never emitted. *(D4)*
14. **Cancel after the last stage does not discard work:** a `should_cancel` that only returns
    `True` after `ANALYSE_STAGES` is exhausted lets `analyse()` return a valid result. *(D6.5 —
    pins the "finished work is still persisted" rule Wave 2 depends on)*
15. `analyse()` does not wrap non-cancel failures: a separator that raises `RuntimeError`
    propagates that `RuntimeError` unchanged.

### `tests/test_environment.py`

16. Stack absent (monkeypatch `importlib.util.find_spec` to return `None` for
    `audio_separator`): `separation_stack` check `ok is False`, `severity is Severity.BLOCK`,
    and `report.can_proceed is False`. *(D12, D13)*
17. `find_spec` is called with each of `_SEPARATION_MODULES` and the check's result flips with
    its return value — proving no real import happens. Assert the *calls*, not `sys.modules`
    (torch may already be loaded by another test).
18. Everything present, one `.wav` given: `blockers == []`, `warnings == []`,
    `can_proceed is True`, `usable_files == [that wav]`.
19. ffmpeg absent (monkeypatch `shutil.which` → `None`): `ffmpeg` check is `Severity.WARN`,
    **not** BLOCK; `can_proceed` still `True` given a `.wav`; an `.m4a` in the same list gets
    `code == "needs_ffmpeg"`; the `.wav` gets `code == "ok"`. With ffmpeg present the `.m4a`
    gets `"ok"`. *(D15)*
20. `model_dir` empty, or containing `htdemucs_6s.yaml` but no `*.th`, or a `.th` below
    10 MB: `model_weights` fails with `Severity.BLOCK`. All three present and large enough:
    passes. *(D14)*
21. `model_dir=None` resolves to `_DEFAULT_MODEL_DIR` — assert by monkeypatching the module
    constant to a `tmp_path`, never by touching the real `C:\tmp\audio-separator-models`.
22. Writability failure: monkeypatch `_is_writable` to return `(False, ...)` and assert
    `stem_cache_writable` and `database_writable` are BLOCK. **Do not attempt to chmod a
    directory read-only** — it does not restrict writes on Windows and the test would pass for
    the wrong reason. *(D17)*
23. `preflight()` creates a non-existent `stems_dir` and the check then passes — the fresh
    install case. *(D17)*
24. Per-file codes: missing path → `"missing"`; a directory → `"missing"`; `notes.txt` →
    `"unsupported_suffix"`; a `.wav` whose `open()` raises `OSError` (monkeypatched) →
    `"unreadable"`.
25. Zero usable files with a clean environment: `blockers == []` but
    `can_proceed is False`. *(D12 — the property that a flat bool would have got wrong)*

### `tests/test_run_batch.py`

26. The three existing tests drop `--no-separate` and the `patch(...NullSeparator)`; patch
    `guitar_helper.run_batch.AudioSeparator` instead and assert `AnalysisPipeline` was
    constructed with `separator=` that instance. Their existing assertions (SKIP printed,
    `run` not called; `--reanalyze` calls `run` once; a failure does not abort the batch) are
    unchanged. *(D11)*
27. **New:** `sys.argv = ["run_batch", folder, "--no-separate"]` raises `SystemExit` — argparse
    rejects the removed flag. This is the test that pins the removal. *(D11)*
28. `_AUDIO_EXTENSIONS` still importable from `run_batch` and equal to
    `environment.SUPPORTED_SUFFIXES`. *(D16 — test line 10 imports it)*

### `tests/test_ui_smoke.py` — line 371 only

29. `AnalysisPipeline(win._app.store, separator=NullSeparator())`, with a **function-local**
    `from guitar_helper.analysis.source_separator import NullSeparator` inside that same test
    function. The existing `pytest.raises(ManualCorrectionsExistError)` assertion is unchanged
    and now proves the guard fires from `precheck()` before separation. Ruff does not lint
    `tests/` and `PLC0415` is not selected, so no `noqa` is needed.

---

## Cross-lane items (§7 conflict protocol)

None of these require changing a frozen contract, so the lane proceeds. All four are reported,
not fixed.

1. **`NullSeparator`'s docstring must say "test-only"** — but `analysis/source_separator.py` is
   **Lane B's file**. Lane C does not edit it. The orchestrator should route this one-line
   docstring change to Lane B or to integration. Suggested text: *"Passthrough — returns the
   input unchanged. TEST AND FIXTURE USE ONLY: analysing a full mix scores 0.463 accuracy
   against 0.821 for a separated stem (ISSUE-007 §2.4). Never construct this for a library the
   user will play from."*
2. **`run_calibrate.py:14`** tells the reader "do NOT pass `--no-separate`". After D11 that
   flag no longer exists, so the line becomes stale. `run_calibrate.py` is unowned in this
   wave — finding only.
3. **`audio_loader._SUPPORTED_NATIVE` / `_SUPPORTED_PYDUB` are imported as private names** by
   `environment.py` (D16). `audio_loader.py` is unowned, so Lane C cannot make them public.
   Wave 2 should promote them and delete the alias in `run_batch`.
4. **`tests/test_ui_smoke.py` gets two lines touched, not one** — the construction call and a
   function-local import in the same test function. Kept inside the function body so the
   module import block, which is where a Lane A collision would occur, is untouched.
