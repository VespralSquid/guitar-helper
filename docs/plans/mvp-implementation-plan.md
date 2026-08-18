# MVP Implementation Plan

_Written 2026-08-04. Supersedes `mvp-release-checklist.md` (merged into this
document). This is the master execution plan: the gates, why they are ordered
this way, and **how** each is accomplished._

**Baseline:** commit `de249c7` — 370 tests passing, committed tree ruff-clean,
Phase 4 M0–M3 + O1–O4 complete.

**Supporting documents.** This plan is the "how"; the reasoning lives elsewhere
and is not repeated here:

| Document | Contains |
|---|---|
| `docs/Report/mvp-readiness-review.md` | Full findings, quality-attribute grades |
| `docs/Report/Guitar_Performance_Assistant_Report_v0.4.md` | As-built architecture |
| `docs/architecture/ARCHITECTURE.md` | Component map, 17 design decisions, threading contract |
| `docs/debug/ISSUE-006-*.md` | Preset-map migration divergence |
| `docs/debug/ISSUE-007-*.md` | No GUI analysis path |
| `docs/debug/ISSUE-008-*.md` | Stem-cache poisoning |
| `docs/plans/gui-analysis-pipeline-plan.md` | Gate 3 in full detail |

---

## 1. Gate summary

| Gate | Objective | Status |
|---|---|---|
| **1** | Data integrity — writes are atomic, preset map is sound | Not started |
| **2** | Separation invariant, stem-cache correctness, track deletion | Not started |
| **3** | GUI can add and analyse songs | Not started |
| **4** | Robustness — no dead ends, no escaped exceptions | Not started |
| **5** | Packaging — a real installable build | Not started |
| **6** | Documentation — user-facing | 3 of 5 done |

## 2. Why this order

Each gate creates the preconditions for the next. Two dependencies are hard:

1. **Gate 3 must follow Gate 1.** GUI analysis makes `save_segments` reachable
   by ordinary users for the first time. On a database missing the `edge` preset
   row (ISSUE-006) an `edge` segment raises `IntegrityError`, and because
   `save_segments` is not transactional that failure destroys the track's
   existing segments. Shipping Gate 3 first converts a latent defect into a
   routine one.
2. **Gate 3's Cancel button must follow Gate 2.** Cancellation is the headline
   trigger for ISSUE-008; adding it before the stem cache is safe turns a
   theoretical corruption into a first-class user action.

Gates 4–6 could run partly in parallel with 3, but 5 depends on knowing what the
app finally imports, so it is cleanest last.

---

## 3. Gate 1 — Data integrity

**Objective:** no write can leave the database partially applied, and every
database converges on the same preset map.

### 3.1 Atomic segment writes (review §B1)

`sqlite3.Connection` is a context manager that commits on success and rolls back
on exception — verified:

```
with c:  insert 1; insert 1  ->  IntegrityError, rows after = 0
```

**Method:**

- `SQLiteSegmentStore.save_segments` — replace the bare `DELETE` /
  `executemany` / `commit()` sequence with a single `with self._conn:` block,
  dropping the explicit commit.
- `EditorState.save()` issues N `update_segment` calls plus M `delete_segment`
  calls, each committing separately, so a failure midway leaves a half-applied
  edit set. Rather than leak the connection into the UI layer, add one method to
  `ISegmentEditor`:

  ```python
  def apply_edits(self, updated: list[Segment], deleted_ids: list[int]) -> None:
      """Persist an edit session atomically — all rows or none."""
  ```

  The SQLite implementation wraps both loops in one `with self._conn:`, and
  `EditorState.save()` becomes a single call. This follows the O3 habit of
  adding capability to the narrow role interface rather than widening access.

**Files:** `db/interfaces.py`, `db/repository.py`, `ui/state/editor_state.py`.

**Verification:** drive `save_segments` with a batch whose second row violates
the `tone_label` foreign key, then assert the pre-existing `manually_corrected`
segment is still present and unchanged. Same shape for `apply_edits`.

### 3.2 Preset reconcile migration (ISSUE-006)

**Blocked on decision D1** (§9).

**Method** (assuming A, the recommendation):

```python
def _migrate_v9_to_v10(conn):
    _add_column_if_missing(conn, "presets", "user_modified",
                           "INTEGER NOT NULL DEFAULT 0")
    conn.executemany("INSERT OR IGNORE INTO presets(...) VALUES (?,?,?)",
                     _DEFAULT_PRESETS)            # adds the missing 'edge' row
    conn.execute("UPDATE presets SET pc_number = pc_number + 1000 "
                 "WHERE user_modified = 0 AND pc_number >= 0")   # trap 1
    for tone, _name, pc in _DEFAULT_PRESETS:
        conn.execute("UPDATE presets SET pc_number = ? "
                     "WHERE tone_label = ? AND user_modified = 0", (pc, tone))
    _relocate_unmapped_presets(conn)                             # trap 2
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_presets_pc "
                 "ON presets(pc_number) WHERE pc_number >= 0")
```

Both traps are reproduced in ISSUE-006 and must be respected. **Trap 1** —
SQLite enforces UNIQUE per row during an `UPDATE`, so reordering in place
collides mid-statement; park values out of range first. **Trap 2** —
`CREATE UNIQUE INDEX` fails on pre-existing duplicates (the surviving `ambient`
case), so leftovers must be relocated before the index is created.

`save_preset` sets `user_modified = 1` when the write comes from the Output panel.

**Files:** `db/schema.py`, `db/repository.py`, `ui/modes/output.py`.

### 3.3 Tests

The gap that allowed ISSUE-006 is that `test_migration_upgrades_to_current`
asserts the version, two column names, and one surviving row — nothing about
presets. Add:

1. Migrated `presets` contents equal fresh `presets` contents, for each legacy
   start version. *(This one test would have caught the entire issue.)*
2. An `edge` segment is storable after migrating a pre-Phase-2 database.
3. No duplicate `pc_number` after migrating a v1 DB holding an `ambient` segment.
4. A `user_modified = 1` row with a non-default PC survives untouched.
5. Migrating a database in the exact `0922b7c` ordering completes without an
   `IntegrityError` (trap 1 regression).

### 3.4 Exit criteria

- Five migration tests plus both transaction tests green; suite still passes.
- Live `library.db` backed up, migrated, verified: 9 tracks / 123 segments /
  123 corrections intact.
- Uncommitted `#sample rate` trailing whitespace stripped; `ruff check
  guitar_helper/` clean before pushing.

---

## 4. Gate 2 — Separation invariant, stem cache, deletion

**Objective:** the full mix can never be analysed, a cached stem can be trusted,
and a song can be removed.

### 4.1 Enforce the invariant

`AnalysisPipeline.__init__` currently does `separator or NullSeparator()`, so
omitting the argument silently analyses the full mix — measured at accuracy
0.463 against 0.821 for stems.

**Method:** make `separator` a **required** parameter. Not "default to
`AudioSeparator`", which would make tests attempt real separation. Required
forces every call site to state its intent.

**Cost is known and small:** 8 of the 9 test call sites construct
`AnalysisPipeline(store)` today and will need an explicit `NullSeparator()`.
That churn is the point — it makes visible how many places silently relied on
full-mix analysis.

`NullSeparator`'s docstring must say it is test-only and never for production
analysis. Remove `--no-separate` from `run_analysis.py` and `run_batch.py`, or
rename it `--debug-full-mix` with a printed warning.

### 4.2 Stem cache correctness (ISSUE-008)

**Method — three layers, cheapest first:**

1. **Atomic publish.** Point the separator's `output_dir` at
   `<cache>/.tmp-<hash>-<pid>/`, `fsync`, then `os.replace()` the produced file
   to `<hash>_guitar.wav`. Atomic on Windows and POSIX within one volume.
   Eliminates every truncation case at the source and makes concurrent
   separation of the same hash harmless. Sweep stale `.tmp-*` dirs at startup.
2. **Completion manifest.** Write `<hash>_guitar.json` *after* the wav is in
   place, carrying `cache_format_version`, `source_hash`, `source_duration_ms`,
   `stem_bytes`, `stem_frames`, `model`, `model_file_hash`, `separator_version`.
   Its presence is the done-marker; its contents answer provenance.
3. **Validation on hit.** Manifest exists and parses, versions and `source_hash`
   match, and `os.stat().st_size == stem_bytes` (0.062 ms). Keep an `sf.info`
   duration cross-check against `tracks.duration_ms` (0.139 ms).

A duration check alone is necessary but not sufficient — verified: a zero-filled
same-size stem passes it and makes every segment `other`, which disables MIDI
dispatch for the whole song.

**Grandfathering:** the nine existing stems have no manifest. Validate each
against `tracks.duration_ms` once, synthesise a manifest marked
`"grandfathered": true`, and accept. Re-separating them would cost minutes each
for stems that are almost certainly fine.

**Also:** move the dev stem cache out of OneDrive (`GUITAR_HELPER_HOME` or
`--stems-dir`). 400 MB of regenerable data is syncing, and Files On-Demand can
dehydrate a stem into something that exists with the right size but needs a
network fetch.

### 4.3 Track deletion

`delete_track` does not exist anywhere. Add it to a store role (a new
`ITrackEditor`, per the O3 segregation habit) plus a Home right-click
"Remove from library".

**Method:** one transaction deleting `segments`, `segments_calibration` and
`playlist_tracks` rows for the hash before the `tracks` row —
`playlist_tracks.file_hash` has **no** `ON DELETE CASCADE` (only `playlist_id`
cascades), so a bare `DELETE FROM tracks` fails while any playlist holds the
song.

Confirmation must name the count: *"This will permanently delete 14 corrected
segments."* The cached stem is **kept**, so remove-and-re-add resets labels
without paying for separation again.

### 4.4 Exit criteria

- Constructing `AnalysisPipeline` without a separator is a `TypeError`.
- Truncated, zero-filled, manifest-less and wrong-provenance stems are all
  rejected as misses.
- A simulated kill mid-separation leaves nothing at the cache path.
- A track in a playlist can be removed without an FK error, transactionally.

---

## 5. Gate 3 — GUI analysis pipeline (ISSUE-007)

**Objective:** a user with no terminal can go from an empty library to analysed,
playable, correctable songs.

Full design in `gui-analysis-pipeline-plan.md`. Method in brief:

**Split the pipeline** — the M0 decode/attach pattern applied to Tier 1. The
pipeline reads the store at the start and writes at the end, and the Qt main
thread is the sole SQLite owner, so:

```python
analyse(path, ...) -> AnalysisResult   # pure computation, worker-safe
persist(result, source_path) -> None   # main thread only
run(path, ...)                         # guard -> analyse -> persist; CLIs unchanged
```

Keeping `run()`'s signature means both CLIs and their tests are untouched.

**Progress is stage-level only** — verified that `Separator.separate()` exposes
no progress or cancel hook. Stages: hashing, separating, decoding, features,
segmenting, classifying, saving, plus *n of m* files. Cancellation is honoured
between stages and between files; the UI must say "Finishing current song…"
rather than freeze.

**Preflight before any expensive work:** separation stack importable, bundled
weights present, ffmpeg on PATH, cache and DB dirs writable, per-file suffix and
readability. Absence of the stack **blocks** ingestion — there is no
proceed-anyway path under the invariant.

**Duplicate handling:** hash first (0.05 s), then decide. Not in DB → analyse.
In DB without corrections → skip unless "re-analyse existing" is ticked. In DB
**with** corrections → always skip, and never offer `discard_corrections` from
the GUI.

**UI:** "Add songs…" in Home plus a right-click entry; progress dialog with
Cancel; **rename the existing "Analyze" button to "Correct labels"**, since it
opens already-analysed tracks and would otherwise be irreconcilably confusing
next to a real analyse action. On finish, `home.refresh()` — which also fixes
review finding H4.

**Testing:** never run real analysis. A track is ~11 s without separation and
minutes with it, against a 40 s suite. Inject a stub pipeline returning a canned
`AnalysisResult`.

**Live verification (resolves D3):** real multi-song add, a remove-and-re-add, a
cancel mid-separation followed by a clean re-run, and playback-during-analysis
**checked by ear** — ISSUE-005's lesson is that a diagnosis from code inspection
is a hypothesis, not a cause.

---

## 6. Gate 4 — Robustness

| Finding | Method |
|---|---|
| **H1** — app refuses to start without loopMIDI | On `MidiPortNotFoundError`, fall back to a null port and show a persistent "MIDI disabled" banner. Port selection belongs in Output mode, beside the test-send button that already diagnoses this |
| **H2** — only `NoSegmentsError` caught around `attach()` | Catch broadly, show a dialog, leave the previous track playing. An escaped exception in a Qt slot typically aborts the process |
| **H3** — correction CLI skips `ensure_calibration_copy` | Call it in `SegmentCorrectionTool`'s save path, matching `EditorState.save` |
| **H5** — bare `except Exception` reports "playlist already exists" | Catch `sqlite3.IntegrityError` specifically; let other errors surface |

---

## 7. Gate 5 — Packaging

**Objective:** an installable build that works on a machine with no Python.

| Item | Method |
|---|---|
| Project metadata | `pyproject.toml` with pinned deps. **Drop `pyqtgraph` and `requests`** — unused since the waveform was removed and lyrics deferred |
| Namespace package | Add `guitar_helper/ui/state/__init__.py`; PyInstaller routinely misses implicit namespace packages |
| App data | `%LOCALAPPDATA%\GuitarHelper`, seeded on first run. Remove CWD-relative defaults (`archetypes.json`, `stems`, `library.db`); `GUITAR_HELPER_HOME` still overrides |
| Calibration | Bundle and seed `archetypes.json` (3 KB). Without it, classification silently falls back to uncalibrated defaults |
| Separation model | Bundle the htdemucs_6s weights (~52 MB); set `model_dir` explicitly. They currently land in `C:/tmp/audio-separator-models/`, which cleanup tools remove |
| Runtime stack | Bundle torch + onnxruntime. Separation is mandatory, so a first-run download would block every new user |
| **Size spike** | **Measure the frozen build early.** torch's PyInstaller footprint is the largest unknown in the estimate; discovering it at the end of this gate is the expensive way |
| Clean-machine test | No Python, no venv, no MSVC. `python-rtmidi` builds from source in dev — the frozen build must not need to |
| Docs | README, LICENSE, setup guide: loopMIDI, **VST2/standalone not VST3**, ffmpeg per D2 |

---

## 8. Gate 6 — Documentation

| Item | Status |
|---|---|
| Report v0.4 reconciled to as-built | **Done** |
| `CLAUDE.md` status, file map, ISSUE-006 caveat | **Done** |
| `ARCHITECTURE.md` as-built map | **Done** |
| User guide for the three modes + a Help entry point | Outstanding |
| Final `SAVE_STATE.md` pass | Outstanding |

---

## 9. Open decisions

| # | Decision | Blocks | Recommendation |
|---|---|---|---|
| **D1** | ISSUE-006 reconcile: **A** (`user_modified` + banner) / **B** (legacy fingerprint) / **C** (detect-only) | **Gate 1 — needed first** | **A** — retires the bug class rather than this instance |
| D2 | Bundle ffmpeg, or detect and link | Gate 5 | Bundle if licensing permits; `.m4a` is the primary format |
| D3 | Pause playback during analysis | Gate 3 step 8 | Decide from the listening test, not speculation |
| D4 | GUI re-analysis of corrected tracks | Gate 3 | No, for MVP — the CLI flag stays the deliberate escape hatch |
| D5 | Does removal clear the cached stem | Gate 2 | Keep it — re-add is then fast, and stems self-validate |

Settled: no songs or database ship · separation mandatory · weights and torch
stack bundled · app data in `%LOCALAPPDATA%` · removal warns with a
corrected-segment count then deletes.

---

## 10. Explicitly out of scope

Recorded so they do not creep in: the QOL pass
(`Phase_4_QOL_changes.md`) · latency measurement on the rig (the offset is a
persisted knob with a working default) · review findings M1–M7 · the
`MainWindow` SRP extraction · moving `ui/editor/` to fix the Tier 1 → Tier 3
import · lyrics, spectrum, cover art, queue persistence · `ambient` re-add ·
more `overdrive` labels · stem-cache eviction.

## 11. Known unknowns

1. **Frozen build size and startup time with torch bundled** (Gate 5).
2. **Whether analysis-during-playback causes audio dropouts** (Gate 3) — the
   ISSUE-005 failure mode was GIL contention, and analysis is the heaviest
   in-process work attempted so far.
3. **Whether the app runs on a machine with no dev toolchain** (Gate 5).
