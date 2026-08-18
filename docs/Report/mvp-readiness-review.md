# MVP Readiness Review — Code & Documentation

_Reviewed 2026-08-04 at commit `de249c7`. Scope: every module under
`guitar_helper/`, the test suite, CI, requirements, and all of `docs/`.
Companion document: `docs/architecture/ARCHITECTURE.md` (as-built inventory)._

**Verification performed:** full test suite run (370 passed, 39.5s), `ruff check`
on `guitar_helper/` and `tests/` (working tree *and* the committed blob), live
`library.db` inspection, `git`-archaeology of every historical change to
`_DEFAULT_PRESETS`, and five purpose-written simulations against throwaway
databases to confirm the migration and transaction findings rather than assert
them from reading.

---

## 1. Verdict

The system is architecturally sound and unusually well reasoned for its size.
The tier boundaries hold, the threading invariants are real and documented at
the point of use, and the Qt-free state core is a genuine asset. The debug
reports (ISSUE-001…005) are better than most commercial post-mortems.

It is **not yet packageable**, for two reasons that are independent of code
quality: there are no packaging artifacts of any kind, and there are two
confirmed data-integrity defects — one of which is the same failure class that
destroyed 129 manual labels in June.

| Attribute | Grade | One-line justification |
|---|---|---|
| Reliability | **C+** | Excellent thread discipline undercut by a non-atomic bulk write and a divergent migration path |
| Robustness | **B−** | Fails fast and loudly in most places; a few unguarded paths crash the GUI or refuse to start |
| Maintainability / Modifiability | **A−** | Clear seams, small modules, rationale captured in comments; one oversized class and one inverted dependency |
| SOLID compliance | **A−** | ISP and DIP are applied deliberately, not decoratively; two type-hint gaps and one deprecated alias still in use |
| Testability | **A** | 370 tests, Qt-free cores, mock seams at every boundary |
| Portability / Deployability | **D** | No packaging, CWD-relative resources, namespace package, hard MIDI dependency at startup |

---

## 2. Blockers — must fix before an MVP build

### B1. `save_segments` is not atomic — partial overwrite silently destroys ground truth

`db/repository.py:43-66` issues `DELETE` then `executemany(INSERT)` then
`commit()`. If any row fails, the `DELETE` and the rows that already succeeded
stay in an open transaction, and **the next unrelated write commits them**.

Demonstrated on a throwaway DB:

```
before:        [('metal', manually_corrected=True)]     ← ground truth
save_segments(clean, ambient) → IntegrityError: FOREIGN KEY constraint failed
after failure: [('clean', False)]                        ← ground truth gone
persisted:     [('clean', 0)]                            ← committed by the next write
```

The pre-write guard (`ManualCorrectionsExistError`, D10) protects against the
*intentional* overwrite that caused the June incident. It does not protect
against this one, because the guard passes and the write then fails halfway.

**Fix:** wrap the delete+insert in an explicit transaction (`with self._conn:`)
so a failure rolls back to the prior state. The same applies to
`EditorState.save()` (`ui/state/editor_state.py:220-231`), which commits
per-row across an update loop and a delete loop with no rollback between them.

**Severity: critical.** Silent, and it targets exactly the data that is
expensive to recreate.

### B2. The migration chain does not converge on the seeded preset map

> Written up in full, with reproduction script, migration sketch, the two SQLite
> traps in it, and a test plan: **`docs/debug/ISSUE-006-preset-map-migration-divergence.md`**.
> Summary below.

`_seed()` runs **only when `schema_version` has no row** — i.e. only on a
brand-new database. `_DEFAULT_PRESETS` is therefore a *creation-time fixture*,
not a converging target. Every time that list changed, existing databases kept
whatever they were born with unless a migration explicitly moved them.

Tracing every historical change:

| Commit | `_DEFAULT_PRESETS` | Version bump? | Migration? |
|---|---|---|---|
| `82542df` initial | clean 0, crunch 1, metal 3, ambient 4 | v1 | — |
| `d4a732e` | clean 0, crunch 1, **metal 2, ambient 3** | **no** | **no** |
| `3ee7d86` | + **edge 4** | **no** | **no** |
| `0922b7c` Phase 3 | clean 0, crunch 1, metal 2, **edge 3, overdrive 4** | v6, migrations 2–6 | yes |
| `de249c7` current | **clean 0, edge 1, overdrive 2, crunch 3, metal 4** | v9 | **no** |

Three of five changes shipped with no migration. Migrations 2–6 were written to
reproduce the `0922b7c` layout and do so correctly — but only for a database
that already had an `edge` row.

This yields **three populations**, not two:

**Ancient (born `82542df`/`d4a732e`)** — migrating to v9 gives
`clean 0, crunch 1, metal 3, overdrive 4` and **no `edge` row at all**.
`_migrate_v4_to_v5` (`UPDATE presets SET pc_number=3 WHERE tone_label='edge'`)
is a silent no-op against a row that was never seeded. Consequence:
`INSERT INTO segments … 'edge'` raises
`IntegrityError: FOREIGN KEY constraint failed`, so analysing any song with an
edge section fails — and by B1 that failure eats the track's existing segments.
At runtime the dispatcher logs `UNMAPPED` and holds.

**Phase 3-era (born `0922b7c`; v6/v7/v8) — the realistic case.** Simulated:

```
DB created at Phase-3-readiness (v6):  clean 0, crunch 1, metal 2, edge 3, overdrive 4
Same DB after migrating to v9:         clean 0, crunch 1, metal 2, edge 3, overdrive 4
```

Unchanged — migrations 7, 8 and 9 only create tables, so the gain-ramp reorder
never reaches it. Every tone present, no crash: it simply **sends the wrong
Program Change for every tone except `clean`**. Silent, and detectable only by
ear.

**Fresh v9** — correct.

**This already caused a documentation error.** `SAVE_STATE.md:83` recorded a
"CORRECTION" stating that `clean=0, crunch=1, metal=2, edge=3, overdrive=4` was
wrong. That is exactly the Phase 3-era migrated layout — the original line was
accurate, and was "corrected" against a freshly-created database. Both
statements were true of different databases, which is precisely this bug's
signature. The live `library.db` was migrated (v6→v8→v9) yet now shows the new
order, so those rows were changed after migration — almost certainly through
the O4 preset table, the only caller of `save_preset`.

**Related — no UNIQUE constraint on `pc_number`.** A v1 DB with an `ambient`
segment keeps `ambient` at PC 4 (`_migrate_v3_to_v4` correctly refuses to delete
a referenced row), and `_migrate_v5_to_v6` then inserts `overdrive` at PC 4 too.
Verified: `DUPLICATE PC numbers: [(4, 2)]`. Only the UI validator guards
uniqueness, and migrations bypass it.

**Fix, and the design question underneath it.** A v10 migration should reconcile
`presets` and add a partial UNIQUE index on `pc_number` where `pc_number >= 0`.
But O4 made presets **user-editable**, so a divergent `pc_number` is ambiguous:
it is either migration drift or a deliberate mapping to the user's actual amp
patch slots, and *the schema records nothing that distinguishes them*. Options:

1. **Add the missing distinction** — a `presets.user_modified` flag set by
   `save_preset` when the write originates in the Output panel. Reconcile then
   becomes unambiguous permanently: correct anything not user-modified, never
   touch anything that is. One column, one migration, retires the whole bug
   class.
2. **Legacy-fingerprint reconcile** — recognise the two known-bad layouts and
   rewrite only those. No schema change, but a fixed list needing extension
   every time the defaults move.
3. **Detect and report** — migration inserts only genuinely missing tone rows
   (fixing the `edge` crash); an Output-mode banner flags divergence and offers
   "reset to defaults".

Recommended: **1 + 3**. The flag makes future reconciles correct by
construction; the banner handles databases that already exist and have no
`user_modified` history to consult. The UNIQUE index should land regardless.

**Severity: critical for anyone upgrading an existing library.**

### B3. Uncommitted lint failure in the working tree

```
W291 Trailing whitespace  guitar_helper\playback\audio_buffer.py:19:37
        self.sr = sr    #sample rate 
Found 1 error.
```

**This is a working-tree edit, not a committed defect.** The committed version at
`de249c7` lints clean, so CI on `main` is green and `SAVE_STATE.md`'s "ruff
clean" claim was accurate. The `#sample rate` comment is an uncommitted local
change; committing it as-is would turn CI red.

Two things to note rather than one:

1. Strip the trailing whitespace before committing (or drop the comment — the
   attribute is already documented in the class docstring, and the project
   convention is no comments unless the *why* is non-obvious).
2. `ruff check` is not being run locally before commits. A pre-commit hook, or
   simply running the CI command before pushing, closes the gap between "the
   save state says clean" and "the pipeline says clean".

Downgraded from blocker to hygiene.

### B4. The GUI cannot add or analyse songs — there is no path from install to use

Added 2026-08-04, after this review's original pass. The review evaluated
components and missed that the product has no entry point: `guitar_helper/ui/`
contains **zero** references to `AnalysisPipeline`, so analysis is CLI-only. A
packaged `.exe` opens to an empty library and instructs the user to run a Python
module. Home's "Analyze" button is misnamed — it opens *already-analysed* tracks
for correction.

Confirmed product decision: no songs and no database ship; the library is
entirely user-supplied. That makes GUI ingestion a release gate.

Full write-up: **`docs/debug/ISSUE-007-no-gui-analysis-path.md`**.
Implementation plan: **`docs/plans/gui-analysis-pipeline-plan.md`**.

Note the ordering constraint: GUI analysis makes `save_segments` reachable by
ordinary users, so **B1 and B2 must land first** or a routine add-songs run can
destroy existing segments.

### B5. Nothing exists to package with

No `pyproject.toml`, `setup.py`, `README`, `LICENSE`, or PyInstaller spec —
`git ls-files` outside `guitar_helper/ tests/ docs/` returns only config files.
Four sub-issues that will each bite during a PyInstaller run:

- **`guitar_helper/ui/state/` has no `__init__.py`** (implicit namespace
  package). PyInstaller's module graph routinely misses these.
- **Resources default to the CWD.** `AppConfig.resolve` falls back to
  `Path.cwd()`, so a frozen `.exe` looks for `library.db`, `music/`, `stems/`
  and `archetypes.json` wherever the user's shell happens to be. A shipped app
  needs a per-user data dir (`%LOCALAPPDATA%`) with first-run seeding.
- **`ThresholdClassifier` defaults `calibration_path="archetypes.json"`**, a
  bare relative path, and `AudioSeparator` defaults `cache_dir="stems"`. Both
  are correctly overridden by every CLI, but the defaults are CWD traps.
- **Dead weight in `requirements.txt`:** `pyqtgraph` (unused since ISSUE-005
  removed the waveform) and `requests` (for the unimplemented lyrics tier).
  Both would be bundled into the exe. `librosa` + `PySide6` alone will make
  this a large binary; don't add to it.

---

## 3. High-priority findings

### H1. The app refuses to start without loopMIDI

`Application.__init__` constructs `MidoPort` eagerly, which raises if the named
port is absent; `ui/app.run` catches it and returns exit code 2. So a user who
only wants to analyse tracks or correct labels — no amp connected — cannot open
the app at all. `--mock` exists but is a CLI flag, not a recoverable path.

**Fix:** on `MidiPortNotFoundError`, offer to continue with a null port and a
persistent "MIDI disabled" banner. Port selection belongs in Output mode
alongside the test-send button, which is already the diagnostic surface for
exactly this problem.

### H2. Only `NoSegmentsError` is caught when attaching a track

`main_window.py:262-268`. Any other exception from `attach` — a corrupt buffer,
a store error, a device failure inside `PlaybackEngine` construction —
propagates out of a Qt slot. In PySide6 that typically aborts the process
rather than surfacing a dialog. The load path is the most failure-prone path in
the app and it is guarded against exactly one failure mode.

### H3. The correction CLI bypasses the calibration snapshot

`EditorState.save()` calls `ensure_calibration_copy` before writing (D11).
`SegmentCorrectionTool.run()`'s `s` command (`correction/cli.py:146-150`) does
not. The same edit made through the CLI loses the pre-edit snapshot that
calibration depends on. Two write paths, two different contracts.

### H4. Home mode never refreshes after corrections are saved

`_bridge.savedChanged` only shows a status-bar message. The
`corrected_count/total_count` progress column and the
"N fully corrected" stats line stay stale until the app restarts — on a screen
whose entire purpose is tracking labelling progress. Connect `savedChanged` to
`home.refresh()`.

### H5. Broad exception handler misreports playlist failures

`home.py:175-179` catches bare `Exception` from `create_playlist` and always
reports "A playlist named X already exists." A locked DB, a disk error, or a
schema problem all surface as a duplicate-name message. Catch
`sqlite3.IntegrityError` specifically.

---

## 4. SOLID assessment

This is the strongest dimension of the project, and unusually it is applied
rather than merely documented. Findings are refinements, not repairs.

**Single Responsibility — strong, one exception.** Modules are small and
purpose-shaped: `dispatch_log.py` (61 lines) does one thing; `merge.py` (40)
does one thing. The exception is `MainWindow` (441 lines), which owns the mode
shell, the async load pipeline, queue synchronisation, playlist-analysis
context, two timers, loop checking, dispatch-log draining and track-finished
detection. `_sync_queue_after_load` / `_commit_playlist_context` /
`_update_playlist_header` are a coherent cluster that would extract cleanly
into a `SessionCoordinator`. Not urgent; it is the file most likely to resist
the QOL pass.

**Open/Closed — genuinely satisfied.** Five real extension seams
(`BaseToneClassifier`, `ISourceSeparator`, `IMidiPort`, the store role ABCs,
the `settings` key/value table). The settings table is the best of these: a new
knob costs a key, not a migration — a lesson clearly learned from the nine
migrations that preceded it.

**Liskov — satisfied.** `NullSeparator`/`AudioSeparator` and
`MockMidiPort`/`MidoPort` are behaviourally substitutable, and the test suite
depends on that being true rather than merely asserting it.

**Interface Segregation — the standout.** The O3 split of `ISegmentStore` into
four role ABCs is real segregation with a real payoff: `MidiDispatcher` depends
on `IPresetStore` (2 methods) rather than an 11-method god interface, and
`SegmentLookup` on `ISegmentReader`. The O4 note in `interfaces.py:178-182` —
that folding settings into `ISegmentStore` broke a test double, which *is* the
ISP violation — is the correct diagnosis for the right reason.

Two loose ends:
- `EditorState` still type-hints the deprecated `ISegmentStore` alias though it
  needs only reader + editor + catalog. Same for `AnalysisPipeline`.
- `HomeMode.__init__(self, store, ...)` and `OutputMode.__init__(self, store, ...)`
  have **no type hint at all**. Home needs `ITrackCatalog + IPlaylistStore`;
  Output needs `IPresetStore`. The segregation exists but isn't expressed where
  a reader would look for it.

**Dependency Inversion — satisfied, with one inverted package edge.**
`Application` is a real composition root and every hardware-touching dependency
is injectable. The exception: **`correction/cli.py` (Tier 1) imports
`guitar_helper.ui.editor.validation` (Tier 3).** The module is Qt-free — I
verified importing the CLI does not pull PySide6 — so this is a naming/layering
defect rather than a runtime one. But the stated rule is that lower tiers have
no knowledge of higher tiers, and this edge points the wrong way. Moving
`ui/editor/{validation,merge,preset_validation}.py` to a neutral
`guitar_helper/editing/` would fix it and cost three import lines.

---

## 5. Reliability & robustness details

**What is done well and should be preserved as-is:**

- The audio callback is disciplined: one lock, one slice copy, a single float
  write for instrumentation, `queue.Full` swallowed, `CallbackStop` at the tail.
  The comment at `playback_engine.py:50-53` explaining *why* the blocksize
  stays tight is exactly the kind of note that prevents a regression.
- `viz_queue=None` in `attach` because there is no consumer — a fix for a real
  per-callback cost, correctly reasoned.
- `DispatchLogBuffer` records *every* decision, not just sends, so "the amp is
  silent" and "the dispatcher is silent" are distinguishable. That is
  diagnosability designed in rather than bolted on.
- `MidoPort` fails at construction rather than mid-song.
- `SegmentLookup`'s snapshot and `set_pc_map`'s plain-dict signature both
  enforce the same invariant (dispatcher thread never touches SQLite) from two
  different directions.

**Remaining rough edges (medium severity):**

| # | Location | Issue |
|---|---|---|
| M1 | `editor_state.py:212, 220, 234` | `set_excluded` / `save` / `discard` use `self._file_hash` with no `None` guard; with no track loaded these issue SQL against `None` |
| M2 | `main_window.py:419-430` | `_check_track_finished` fires if the user pauses within 50 ms of the end — auto-advances against intent |
| M3 | `queue_state.py:131, 173` | `list.index(current)` uses dataclass equality; a duplicated track in the queue resolves to the wrong index |
| M4 | `pipeline.py:68-69` | `extract()` and `extract_for_classification()` each call `_stack_raw()`, so the librosa stack is computed twice. **Measured, not estimated:** one `_stack_raw` is 0.77 s against a 10.7 s no-separation analysis of a 189 s track — a 7% saving, and negligible once separation (minutes) dominates. An earlier draft of this review called it "~2× analysis time"; that was wrong. Worth doing as tidiness, **not** as a performance fix |
| M5 | `schema.py:121-124` | `_add_column_if_missing` builds DDL with f-strings. Values are internal constants so it is not injectable, but it contradicts the project's own "parameterized SQL only" rule and should carry a note saying why it's exempt |
| M6 | `run_analysis.py`, `run_batch.py`, `run_calibrate.py` | never close their sqlite connection |
| M7 | `application.py:158-162` | `shutdown()` calls `port.close()` unguarded; if the port is already closed or wedged, close fails during window teardown |

---

## 6. Test suite assessment

370 tests, all passing, ~40 s. Coverage is broad and well targeted — the
Qt-free cores (`editor_state`, `queue_state`, `validation`, `merge`) are
thoroughly unit-tested, and `test_ui_smoke.py` drives 28 genuine end-to-end
wiring paths through pytest-qt (relabel → save → store round-trip, boundary
drag round-trip, orphan-free reload, dispatch-timer visibility gating). This is
the reason the codebase can be refactored with confidence.

Gaps, in priority order:

1. **Migration tests never assert preset convergence.**
   `test_migration_upgrades_to_current` checks the version number, two column
   names, and that one track row survived. Nothing checks the preset table. B2
   would have been caught by a single test asserting that a migrated DB and a
   fresh DB have identical `presets` contents. Add that test with the fix.
2. **No `save_segments` failure test.** No test drives a partial-write failure,
   which is why B1 is invisible.
3. **No `tests/test_audio_loader.py`** — the module that owns file identity,
   duration and every decode path is untested. Format dispatch, unsupported
   suffix, and missing file are all cheap to cover.
4. **Entry points untested:** `run_analysis`, `run_calibrate`, `run_correction`,
   `run_playback`, `run_ui`, `measure_latency`. Only `run_batch` has tests. For
   an MVP these are the user's actual interface.
5. `latency_probe.py`, `ui/theme.py`, `ui/load_worker.py` and
   `ui/app.py` have no direct tests.
6. CI lints `guitar_helper/` only, not `tests/`.

---

## 7. Documentation assessment

**Excellent — keep and continue the practice:** the `docs/debug/ISSUE-NNN`
series, `docs/experiments/EXP-001`, `latency-calibration-analysis.md` and
`phase4-rework-report.md`. These record *why*, name the discriminating
observation, and are honest about what was tried and rejected.
`phase4-rework-report.md` §2.1 — deriving three user-reported bugs from two
structural causes — is genuinely exemplary.

**The problem is the flagship document.** `Guitar_Performance_Assistant_Report_v0.3.md`
(2026-06-11, 495 lines) is the report a reader is most likely to open first,
and it now describes a system that does not exist:

| Report v0.3 says | Reality |
|---|---|
| Tones: `clean / crunch / heavy / ambient / other` | `clean / edge / overdrive / crunch / metal / other` |
| `segments.preset_name TEXT NOT NULL` column | No such column; preset name lives in `presets` |
| `AudioLoader` returns `(y, sr, duration_ms)` | `load() → (duration_ms, hash)`, `load_mono()`, `decode()` |
| `SegmentLookup` issues SQL per query | In-memory bisect over a construction-time snapshot (D6) |
| `IRenderer`, `SpectrumAnalyzer`, `WaveformView`, `SegmentOverlay`, `LyricsParser`, `LyricsDisplay` | None exist. Waveform and overlay were deleted by the ISSUE-005 fix |
| `IPlaybackPositionProvider`, `IAudioChunkProvider` | Do not exist; `PositionTracker` is used concretely |
| `confidence < 0.5` flagged for review | Threshold is `_CONFIDENCE_FLOOR = 0.2`, and it routes to `other` rather than flagging |
| Ableton Live 12 Lite + Neural DSP via VST | Cantabile + VST2/standalone — VST3 cannot work (ISSUE-004) |
| Open Question 6: "Distribution: PyInstaller .exe" | Still open, unchanged, and now the critical path |
| Sections 8.2–8.4 SOLID examples | Cite classes that don't exist; the *real* ISP story (the O3 role split) is better and absent |

The irony is that the shipped design is stronger than the documented one —
the O3/O4 interface work is the best SOLID material in the project and appears
nowhere in the report.

Smaller drift:

- **`CLAUDE.md` Key File Map** lists `lyrics/ — LrcParser, LrcLibClient`.
  `guitar_helper/lyrics/__init__.py` is 0 bytes; nothing else is in the package.
- **`CLAUDE.md` "Current status"** still reads "Phase 2 in progress" while
  Phase 4 O1–O4 are complete.
- **`SAVE_STATE.md`** claims ruff clean (B3) and does not mention that the
  `settings` table is empty in the live DB — the dispatch offset has never been
  applied, so the persisted-knob path is untested in production.
- ~~**`docs/debug/List of known errors`** has no file extension and lists three
  bugs all since resolved; it reads as open.~~ **Fixed 2026-08-04** — renamed to
  `docs/debug/user-reported-errors-phase4.md` with a resolution table; all
  cross-references updated.

**Missing entirely:** README, LICENSE, install/setup guide (ffmpeg + loopMIDI +
rtmidi source build + the VST2-not-VST3 constraint are all prerequisites a new
user cannot discover), and a user guide for the three modes. The QOL spec
already anticipates this — "help tab to redirect to repo or documentation (to
be created later, such as README…)".

---

## 8. Repository hygiene

- Five stray databases at root: `library-NeverFadeAway.db`,
  `library-NeverFadeAway-2.db`, `library_separated.db`, and three
  `library.db.bak-*` files. Gitignored, but they will confuse a packaging pass
  and one of them is a stale schema.
- `batch_reanalysis.log` and `print_db.py` at root; `print_db.py` hardcodes
  `library.db` and duplicates logic that `list_tracks()` now provides.
- `.pytest_cache/` and `.ruff_cache/` are present in the working tree.
- `ruff.toml` targets `py312` while the runtime is 3.14.
- `guitar_helper/ui/assets/icons/` is an empty untracked directory;
  `theme.icon()` degrades correctly, so this is only a note for the QOL icon work.

---

## 9. Recommended sequence to MVP

**Gate 1 — data integrity (do first, nothing else is safe until these land)**
1. B1: transaction-wrap `save_segments` and `EditorState.save`; add the
   partial-failure test.
2. B2: v10 migration reconciling `presets` against `_DEFAULT_PRESETS`; UNIQUE
   index on `pc_number >= 0`; a test asserting migrated ≡ fresh.
3. Back up `library.db` before running the v10 migration on it.
4. B3 (hygiene): strip the uncommitted trailing whitespace; run `ruff check`
   locally before pushing.

**Gate 1.5 — the missing entry point (B4 / ISSUE-007)**
Must follow Gate 1: GUI analysis makes `save_segments` reachable by ordinary
users, so it cannot ship before the write is atomic and the preset map is sound.
Full plan in `docs/plans/gui-analysis-pipeline-plan.md`; headline steps:
- Split `AnalysisPipeline` into `analyse()` (worker-safe, no store) and
  `persist()` (main thread) — the M0 decode/attach pattern applied to Tier 1.
- `AnalysisWorker` QThread + add-songs and progress dialogs.
- Preflight for the separation stack and ffmpeg, with an honest warning that
  `NullSeparator` results are *mismatched* against stem-calibrated archetypes,
  not merely weaker.
- Rename Home's "Analyze" button to "Correct labels".

**Gate 2 — packaging**
5. `pyproject.toml` with pinned deps; drop `pyqtgraph` and `requests`.
   Bundle and first-run-seed `archetypes.json` — without it, classification
   silently falls back to the uncalibrated defaults (macro-F1 0.69 → ~0.43).
6. `__init__.py` for `ui/state/`.
7. Per-user data dir with first-run seeding of `library.db` + `archetypes.json`;
   remove the CWD-relative defaults.
8. PyInstaller spec; verify a clean-machine launch.
9. README + LICENSE + setup guide (ffmpeg, loopMIDI, rtmidi, **VST2 not VST3**).

**Gate 3 — robustness**
10. H1 (start without MIDI), H2 (broad attach guard), H3 (CLI snapshot),
    H4 (Home refresh), H5 (specific exception).
11. M4 (single feature-stack pass) — the most user-visible performance win.

**Gate 4 — documentation truth**
12. Report v0.4: correct the taxonomy, schema, component list and SOLID
    sections; document the O3/O4 interface work; close Open Question 6.
13. Update `CLAUDE.md` status + Key File Map; refresh `SAVE_STATE.md`.

**Then** the QOL pass (`Phase_4_QOL_changes.md`) and the outstanding latency
measurement, neither of which is a release blocker.
