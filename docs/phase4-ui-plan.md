# Phase 4 Implementation Plan — PySide6 Prototype Correction/Calibration UI

_Status: APPROVED (planning). No code written yet. Schema v7 approved as planned._

## 0. Framing

Prototype **editor-forward correction tool**, not a shipping product. Every visual
constant (color, font, spacing, icon path, QSS) lives in one `theme.py` so the app
re-skins without touching logic. All real logic lives in **Qt-free, unit-testable
classes** (`EditorState`, `validation`, `merge`, `LrcParser`, store methods); Qt
widgets are thin views that subscribe to state and forward user intent. The UI owns
the single SQLite connection, drives `Application`, and never touches the
engine/dispatcher/DB directly except `Application.store` for main-thread reads/writes.

### Core decisions (rationale one-liners)

| Decision | Choice | Why |
|---|---|---|
| Calibration-copy storage | New `segments_calibration` table, lazy copy on first edit | Keeps `segments` DELETE-then-INSERT intact, survives restart, pristine pre-merge snapshot. Flag-per-row loses boundaries on merge; sidecar JSON breaks the one-connection invariant. |
| Dispatch-event observation | `collections.deque(maxlen=200)` the dispatcher appends to; UI drains on a timer | `append`/`popleft` atomic under GIL — lock-free, adds ~nothing to the dispatcher thread. A logging handler allocates `LogRecord`s. |
| View subscription | Qt-free `EditorState` + thin `EditorStateBridge(QObject)` signals | Core logic importable/testable without a `QApplication`; views still get `connect()`. |

## 1. Package / file layout

```
guitar_helper/ui/
  __init__.py
  app.py                  # build QApplication, Application, MainWindow; run()
  main_window.py          # QMainWindow: menu (File→Open), docks, wiring, timers
  theme.py                # ALL styling: tone→color, QSS, sizes, icon/font slots
  controllers.py          # PlaybackController: transport intent → Application
  state/
    editor_state.py       # EditorState (Qt-free single source of truth)
    state_bridge.py       # EditorStateBridge(QObject): signals over EditorState
    dispatch_log_buffer.py# DispatchLogBuffer (deque ring, written by dispatcher)
  editor/
    validation.py         # Qt-free edit validation (CLI + GUI share this)
    merge.py              # Qt-free merge-consecutive-same-tone logic
    segment_editor.py     # SegmentEditor widget: orchestrates table+overlay edits
  transport.py            # TransportControls widget (play/pause/stop/seek/loop)
  views/
    waveform_view.py      # pyqtgraph waveform + playhead + click-to-seek
    segment_overlay.py    # tone-band LinearRegionItems + draggable boundaries
    spectrum_view.py      # rolling FFT (M7)
    lyrics_view.py        # synced lyrics scroller (M7)
  panels/
    library_panel.py      # track list (list_tracks), selection → load
    preset_panel.py       # presets table view/edit (M6)
    dispatch_log.py       # live dispatch log + active-preset indicator (M6)
  models/
    qt_adapters.py        # QAbstractTableModel for segments & presets

guitar_helper/lyrics/
  __init__.py
  lrc_parser.py           # LrcParser (Qt-free)
  lrclib_client.py        # LrcLibClient (requests, offline-safe)

guitar_helper/run_ui.py   # entrypoint: python -m guitar_helper.run_ui [--mock] [--db ...]
```

**Deviations from the original sketch (justified):**
- `controllers.py` / `PlaybackController` — keeps transport widgets dumb, centralizes
  "intent → `Application.play/pause/seek`" and loop-segment re-seek.
- `editor_state.py` / `state_bridge.py` / `dispatch_log_buffer.py` under `ui/state/` —
  the testable core, grouped.
- `merge.py` beside `validation.py` — M5 logic is non-trivial, own test module.
- `run_ui.py` at package root beside the other `run_*.py` entrypoints.

## 2. EditorState — Qt-free single source of truth

`guitar_helper/ui/state/editor_state.py`. No Qt import. Edits stored segments directly;
never re-runs the segmenter.

```python
class EditorState:
    def __init__(self, store: ISegmentStore, tone_labels: tuple[str, ...]) -> None: ...

    # load / lifecycle
    def load_track(self, file_hash: str) -> None
    def clear(self) -> None

    # read accessors
    @property
    def file_hash(self) -> str | None
    @property
    def segments(self) -> list[Segment]                  # defensive copy
    @property
    def selection_index(self) -> int | None
    @property
    def dirty(self) -> bool
    def segment_at(self, position_ms: int) -> int | None  # bisect → index
    def calibration_copy_exists(self) -> bool

    # selection
    def select(self, index: int | None) -> None

    # edit ops (validate → mutate working list → mark pending+dirty → emit)
    def relabel(self, index: int, label: str) -> EditResult
    def edit_boundary(self, index: int, start_ms: int, end_ms: int) -> EditResult
    def confirm(self, index: int) -> EditResult
    def confirm_all(self) -> EditResult
    def merge_run(self, index: int) -> EditResult          # M5
    def set_excluded(self, excluded: bool) -> None         # eager store write

    # persistence (all on Qt main thread)
    def save(self) -> None    # ensure_calibration_copy() then flush update/delete
    def discard(self) -> None # reload from store, drop pending

    # observation (Qt-free)
    def subscribe(self, callback: Callable[[StateEvent], None]) -> None
```

```python
@dataclass
class EditResult:
    ok: bool
    error: str | None = None   # human-readable constraint message for live feedback

@dataclass
class StateEvent:
    kind: Literal["loaded", "selection", "segments", "dirty", "saved", "excluded"]
```

- Calls `validation.*` before mutating. On failure returns `EditResult(ok=False, error=...)`,
  emits nothing — views show the message inline and revert the visual.
- Pending edits tracked as `dict[int, Segment]` keyed by id + `set[int]` of ids to delete
  (merge produces deletions). `save()` is the only writer: `ensure_calibration_copy()` first,
  then `update_segment` for changed rows, `delete_segment` for merged-away rows.

### EditorStateBridge (Qt)

```python
class EditorStateBridge(QObject):
    loaded = Signal()
    selectionChanged = Signal(object)   # int | None
    segmentsChanged = Signal()
    dirtyChanged = Signal(bool)
    savedChanged = Signal()
    def __init__(self, state: EditorState): ...  # registers state.subscribe(self._on_event)
```

Translates `StateEvent.kind` → matching signal. Views connect to the bridge; only the
editor widget calls `EditorState` edit methods.

## 3. Validation and merge (Qt-free, shared with CLI)

### `ui/editor/validation.py`

Extract constraint logic from `correction/cli.py::_parse_command` into pure functions:

```python
def validate_relabel(label: str, tone_labels) -> EditResult
def validate_boundary(segments, index, start_ms, end_ms) -> EditResult
    # start<end; start >= prev.end_ms; end <= next.start_ms
def apply_relabel(seg: Segment, label: str) -> Segment      # confidence=1.0, corrected=True
def apply_boundary(seg, start_ms, end_ms, label) -> Segment
def apply_confirm(seg: Segment) -> Segment                   # == current cli._confirm
```

**CLI reuse / backward-compat:** refactor `_parse_command` to call these helpers.
**Critical:** `tests/test_correction.py` imports `_parse_command` directly — keep that symbol
and its `tuple[Segment, ...] | None` contract; only its body delegates to `validation`.

### `ui/editor/merge.py` (M5)

```python
def find_same_tone_run(segments, index) -> tuple[int, int]  # inclusive [lo, hi]
def merge_run(segments, lo, hi) -> MergePlan
    # keep = Segment(start=segments[lo].start_ms, end=segments[hi].end_ms,
    #                label=tone, confidence=1.0, manually_corrected=True, id=segments[lo].id)
    # delete_ids = [segments[lo+1..hi].id]
```

Extends the first segment, deletes the rest — never re-segments. Pure function, unit-tested
without the DB.

### Calibration copy — storage (APPROVED)

New sibling table, written once lazily on first edit via `ensure_calibration_copy(file_hash)`.
`schema.py` v7 migration, additive `CREATE TABLE IF NOT EXISTS`, never DROP:

```sql
CREATE TABLE IF NOT EXISTS segments_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash TEXT NOT NULL REFERENCES tracks(file_hash),
    start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
    tone_label TEXT NOT NULL, confidence REAL NOT NULL,
    manually_corrected INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_segcal_hash ON segments_calibration(file_hash);
```

`ensure_calibration_copy` idempotent:
`INSERT ... SELECT FROM segments WHERE file_hash=? AND NOT EXISTS (SELECT 1 FROM segments_calibration WHERE file_hash=?)`.
Phase-3 correction-preservation guard unaffected (we only touch `segments`/`segments_calibration`,
never re-run the segmenter). Follow-up: point `run_calibrate.py` at `segments_calibration` when
present (out of scope for M1–M5).

## 4. Store interface changes

Add to `db/interfaces.py`:

```python
@dataclass
class Track:
    file_hash: str
    filename: str
    title: str | None
    artist: str | None
    duration_ms: int
    source_path: str | None
    calibration_excluded: bool
    corrected_count: int   # manually_corrected=1
    total_count: int       # all segments → "needs labeling" = corrected_count < total_count

# new abstractmethods:
def list_tracks(self) -> list[Track]: ...
def delete_segment(self, segment_id: int) -> None: ...
def ensure_calibration_copy(self, file_hash: str) -> None: ...
def get_calibration_segments(self, file_hash: str) -> list[Segment]: ...
```

`SQLiteSegmentStore` (all parameterized; LEFT JOIN so zero-segment tracks still appear):

```python
def list_tracks(self):
    rows = self._conn.execute("""
        SELECT t.file_hash, t.filename, t.title, t.artist, t.duration_ms,
               t.source_path, t.calibration_excluded,
               COALESCE(SUM(s.manually_corrected), 0) AS corrected,
               COUNT(s.id) AS total
        FROM tracks t
        LEFT JOIN segments s ON s.file_hash = t.file_hash
        GROUP BY t.file_hash
        ORDER BY t.artist, t.title, t.filename
    """).fetchall()
    return [Track(...) for r in rows]

def delete_segment(self, segment_id):
    self._conn.execute("DELETE FROM segments WHERE id = ?", (segment_id,))
    self._conn.commit()

def ensure_calibration_copy(self, file_hash):
    self._conn.execute("""
        INSERT INTO segments_calibration
            (file_hash, start_ms, end_ms, tone_label, confidence, manually_corrected)
        SELECT file_hash, start_ms, end_ms, tone_label, confidence, manually_corrected
        FROM segments WHERE file_hash = ?
          AND NOT EXISTS (SELECT 1 FROM segments_calibration WHERE file_hash = ?)
    """, (file_hash, file_hash))
    self._conn.commit()
```

`schema.py`: bump `_CURRENT_VERSION` to 7, add `_migrate_v6_to_v7` (creates the table), register
in `_MIGRATIONS`, add DDL to `_DDL` so fresh DBs get it. The suite uses a real in-memory
`SQLiteSegmentStore`, so no fake store to update.

## 5. Qt-timer polling design (threading invariant)

Three `QTimer`s on `MainWindow`, started on load, stopped on stop/close:

| Timer | Interval | Drains / does |
|---|---|---|
| `_viz_timer` | ~16 ms (≈60 Hz) | Loop `app.viz.get_nowait()` until None; push `(start_frame, chunk)` to waveform (live) + spectrum FFT buffer |
| `_pos_timer` | ~33 ms (≈30 Hz) | Read `app.tracker.position_ms`; move playhead, update active-segment highlight, scroll lyrics, loop-segment wrap check |
| `_dispatch_timer` | ~100 ms | Drain `DispatchLogBuffer` deque → dispatch_log panel; update active-preset indicator (M6) |

No callbacks on the audio/dispatcher threads. Dispatcher only `append`s to `deque(maxlen=200)`;
audio thread keeps its existing `viz_queue.put_nowait`. All DB writes happen inside timer
ticks / user-event slots on the main thread.

**Loop-current-segment** (`PlaybackController`): when toggled on with a segment selected,
`_pos_timer` checks `position_ms >= loop_segment.end_ms`; if so, `controller.seek(loop_segment.start_ms)`
(→ `Application.seek` → engine clamp + `dispatcher.reset()`). Main-thread re-seek, invariant-safe.

## 6. pyqtgraph specifics

`views/waveform_view.py` (`pyqtgraph.PlotWidget`):
- **Static waveform:** mono mixdown of `AudioBuffer.data` once, then min/max envelope downsample
  to ~3k display columns (per-bin min & max → `FillBetweenItem`/two `PlotDataItem`s). Never plot
  raw frames. Fixed column budget → constant render cost. Recompute only on load.
- **Playhead:** one `pg.InfiniteLine(angle=90, movable=False)`; `_pos_timer` sets `.setValue(position_ms)`
  (x-axis in ms, mapped from frames at load).
- **Click-to-seek:** connect `plotItem.scene().sigMouseClicked`; map scene pos → x (ms) via
  `vb.mapSceneToView`; `controller.seek(ms)`.

`views/segment_overlay.py`:
- **Tone bands:** one `pg.LinearRegionItem` per segment, band fill `movable=False`,
  `brush = theme.tone_brush(label)`, `[start_ms, end_ms]`. Active band highlighted by raising
  alpha/pen (driven by `_pos_timer` + selection signal).
- **Draggable boundaries:** dedicated `pg.InfiniteLine(movable=True)` handles at interior
  boundaries (not region edges → avoids overlapping-region hit-test ambiguity). `sigDragged` →
  `validation.validate_boundary` against neighbours, turn red + tooltip if invalid, clamp visual
  to legal range. `sigPositionChangeFinished` → `EditorState.edit_boundary` (re-validates, persists on save).
- **Selection binding (M3):** table row select → `selectionChanged` → overlay raises band;
  click band → `state.select(index)` → table scrolls/selects. Single source = `EditorState.selection_index`.

**Risks & mitigations:**
- Drag hit-testing / overlapping regions → dedicated `InfiniteLine` handles, one per interior boundary.
- Overlap on commit → `validate_boundary` enforces `prev.end <= start < end <= next.start`; reject + revert.
- Long-track performance → min/max envelope downsample, fixed column budget, rebuild only on load.
- Region count → batch as a single `GraphicsObject` later if ever thousands of segments (not needed now).

## 7. theme.py

Pure data + a couple helpers, no logic.

```python
TONE_COLORS: dict[str, str] = {
    "clean": "#3b82f6", "edge": "#22c55e", "overdrive": "#eab308",
    "crunch": "#f97316", "metal": "#ef4444", "other": "#6b7280",
}
def tone_brush(label): ...      # pg brush w/ BAND_ALPHA
def tone_pen(label): ...

FONT_FAMILY = "Segoe UI"
ICON_DIR = Path(__file__).parent / "assets" / "icons"   # empty → text fallbacks
def icon(name): ...             # QIcon or text label if asset missing

WAVEFORM_COLUMNS = 3000
BAND_ALPHA = 70
PLAYHEAD_WIDTH = 2

APP_QSS = """..."""             # dark prototype skin
```

`app.py` calls `qApp.setStyleSheet(theme.APP_QSS)`. Icons/fonts resolve through `theme.icon()`
with text fallbacks → prototype runs with **zero binary assets**; dropping PNGs into
`ui/assets/icons/` later just works.

## 8. Milestone task list (file-level, demoable outcome)

**M1 — Shell + transport + library + File→Open**
- `run_ui.py`, `ui/app.py`, `ui/main_window.py`, `ui/transport.py`, `ui/panels/library_panel.py`,
  `ui/controllers.py`, `ui/theme.py`; `store.list_tracks()` + `Track`.
- _Demo:_ launch, library lists analysed tracks, double-click/File→Open loads via `Application.load`,
  play/pause/stop/seek work; `NoSegmentsError` shows a dialog.

**M2 — Waveform + playhead + click-to-seek + loop**
- `ui/views/waveform_view.py`, timer wiring in `main_window.py`, loop logic in `controllers.py`.
- _Demo:_ static waveform renders, playhead tracks audio, click-to-seek jumps, loop repeats a region.

**M3 — Segment table + overlay + bound selection**
- `ui/models/qt_adapters.py` (segments `QAbstractTableModel`), `ui/views/segment_overlay.py`,
  `ui/state/editor_state.py` + `state_bridge.py` (load/select only).
- _Demo:_ table + colored tone bands; selecting a row highlights its band and vice-versa.

**M4 — Edit ops (CLI parity)**
- `ui/editor/validation.py` (+ refactor `correction/cli.py`), `ui/editor/segment_editor.py`,
  draggable boundaries, `EditorState` edit methods, `store.delete_segment`, save/discard, dirty indicator.
- _Demo:_ relabel via dropdown, drag boundary with live feedback, confirm/confirm-all, exclude/include;
  Save persists; reload shows changes. Matches CLI behavior.

**M5 — Merge + calibration copy**
- `ui/editor/merge.py`, schema v7 + `segments_calibration`, `ensure_calibration_copy`/
  `get_calibration_segments`, merge action in editor.
- _Demo:_ merge a same-tone run into one; originals preserved in `segments_calibration`;
  re-analysis guard still respects corrected tracks.

**M6 — Preset panel + dispatch log (ISSUE-004 aid)** _(secondary)_
- `ui/state/dispatch_log_buffer.py`, hook `MidiDispatcher` (optional `log_sink=None`), `ui/panels/dispatch_log.py`,
  `ui/panels/preset_panel.py`, `_dispatch_timer`.
- _Demo:_ live scrolling log of every PC sent (or held for `other`) with timestamps + active-preset
  indicator — visualizes whether Nolly ignores Program Changes.

**M7 — Spectrum + lyrics** _(secondary)_
- `ui/views/spectrum_view.py` (rolling FFT), `ui/views/lyrics_view.py`, `lyrics/lrc_parser.py`,
  `lyrics/lrclib_client.py`.
- _Demo:_ live spectrum during playback; local `.lrc` scrolls in sync; missing lyrics auto-fetch
  with graceful offline fallback.

### M6 dispatcher hook

`MidiDispatcher.__init__` gains `log_sink: Callable[[DispatchEvent], None] | None = None` (default
None preserves existing behavior/tests); after each send/hold, `if self._log_sink: self._log_sink(event)`.
Sink is `DispatchLogBuffer.append` — bare `deque.append`, non-blocking, lock-free.

### M7 lyrics

```python
class LrcParser:
    @staticmethod
    def parse(text: str) -> list[LrcLine]       # [mm:ss.xx] → (time_ms, text); ignores malformed
    @staticmethod
    def load(path: str | Path) -> list[LrcLine]

class LrcLibClient:
    def __init__(self, timeout_s: float = 4.0): ...
    def fetch(self, title, artist, duration_s) -> str | None   # None on any network error (offline-safe)
```

## 9. Testing approach (DB/threading invariants honored)

**Unit tests (no Qt, no audio device):**
- `test_editor_state.py` — load, select, edit results, dirty flag, save flushes, discard reloads.
- `test_validation.py` — boundary/relabel constraint matrix (mirrors `test_correction.py`, proves parity).
- `test_merge.py` — run detection + merge plan (delete ids, extended segment).
- `test_repository.py` (extend) — `list_tracks` aggregate counts, `delete_segment`,
  `ensure_calibration_copy` idempotency, `get_calibration_segments`.
- `test_schema.py` (extend) — v6→v7 migration creates table, fresh DB has it, no DROP.
- `test_dispatch_log_buffer.py` — ring maxlen/drain; concurrency smoke.
- `test_lrc_parser.py` — well-formed, malformed, empty, multi-timestamp lines.

**pytest-qt smoke (light, headless):**
- Add `pytest-qt` (test-only) + CI step `QT_QPA_PLATFORM=offscreen` on windows-latest.
- `test_ui_smoke.py` — build `MainWindow` with `MockMidiPort` + in-memory store; assert it builds,
  library populates, selecting a row updates the model, timers start/stop without real audio (don't call
  `Application.play`, or inject a stub engine). Guard with `pytest.importorskip("pytestqt")`.

**Invariants:** no test registers a callback on audio/dispatcher threads; dispatcher `log_sink`
defaults None → 133 existing tests untouched; `_parse_command` symbol/contract preserved.

## 10. Verification checklist

- [ ] `python -m ruff check guitar_helper/` clean (target py312; comment-free except non-obvious WHY).
- [ ] All 133 existing tests still pass.
- [ ] New unit tests pass: editor_state, validation, merge, repository, schema v7, dispatch buffer, lrc_parser.
- [ ] pytest-qt smoke passes headless (`QT_QPA_PLATFORM=offscreen`) on windows-latest.
- [ ] All new SQL parameterized; no f-strings; no DROP+recreate; v7 additive `CREATE TABLE IF NOT EXISTS`.
- [ ] No hardcoded PC numbers in UI — colors from `theme`, PC mapping from `store.get_presets()`.
- [ ] Threading: UI owns the only SQLite connection; DB writes on main thread; audio callback +
      dispatcher loop unchanged except optional lock-free `log_sink`.
- [ ] `theme.py` is the only place with colors/QSS/sizes; app runs with zero binary assets.
- [ ] Manual smoke: `python -m guitar_helper.run_ui --mock`; open track; play/pause/seek; click-to-seek;
      loop a segment; relabel + drag boundary + confirm + exclude; Save; reload verify; merge a run and
      confirm `segments_calibration` retains originals; watch dispatch log fire PCs (ISSUE-004).
- [ ] No "Co-Authored-By" / Claude advertising in commits.

### Critical files for implementation
- `guitar_helper/ui/state/editor_state.py`
- `guitar_helper/db/repository.py`
- `guitar_helper/ui/views/segment_overlay.py`
- `guitar_helper/ui/main_window.py`
- `guitar_helper/correction/cli.py`
