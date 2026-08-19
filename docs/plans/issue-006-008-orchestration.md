# Orchestration — ISSUE-006 / 007 / 008 parallel fix wave

**Created:** 2026-08-18 · **Verified against** `mvp-implementation-plan.md` and the
2026-08-04 save state before launch.
**Scope:** the three open MVP blockers (ISSUE-006, ISSUE-007, ISSUE-008) plus
`mvp-readiness-review.md` §B1, which is a hard prerequisite of both 006 and 007.
**Launch with:** `/issue-wave` (see `.claude/commands/issue-wave.md`).

**Master plan:** `docs/plans/mvp-implementation-plan.md` is the authority on gate content
and ordering. This document does not replace it — it is the execution layout for its
Gates 1–3 only. Lane-to-gate mapping:

| Lane | Master-plan section | Note |
|---|---|---|
| A | Gate 1 §3.1 (atomic writes) + §3.2 (preset reconcile) + §3.3 (tests) | whole of Gate 1 |
| B | Gate 2 §4.2 (stem cache) | **not** §4.3 `delete_track` — see below |
| C | Gate 2 §4.1 (separation invariant) + Gate 3 split/progress/preflight | Qt-free part of Gate 3 |

**Gate 2 §4.3 (`delete_track`) is deliberately deferred to Wave 2.** It needs
`db/repository.py` and `db/interfaces.py`, which Lane A holds for the whole wave, and its
only consumer is a Home right-click that is itself Wave 2. Splitting it out would put two
lanes in one file for no gain in wall-clock time.

**Baseline at launch:** commit `1cd60f4` + one doc commit, branch `fix/issue-006-008-wave`,
**370 tests passing in 27.5 s, `ruff check guitar_helper/` clean.** Any lane that finds a
failing test it did not cause should say so — the baseline was green.

---

## 1. Why these three can run in parallel at all

The published sequencing (`mvp-implementation-plan.md` §2, `mvp-readiness-review.md` §9,
`gui-analysis-pipeline-plan.md` §1) is strictly serial: B1 → 006 → 008 → 007. That ordering
is a **ship gate, not a write gate**. It exists because GUI analysis makes `save_segments`
reachable by ordinary users, so 007 must not *reach a user* before 006 and B1 are sound.
The master plan's two hard dependencies (§2.1 Gate 3 after Gate 1, §2.2 the Cancel button
after Gate 2) are both about what a *user* can reach, and both are honoured: the Cancel
button and every other UI surface live in Wave 2.

Nothing in it requires the code to be *written* serially, because the three fixes touch
disjoint files and communicate only across interfaces that none of them change:

```
Lane A  db/schema.py, db/repository.py, db/interfaces.py, ui/state/editor_state.py,
        ui/modes/output.py
Lane B  analysis/source_separator.py
Lane C  analysis/pipeline.py, analysis/environment.py (new), run_analysis.py, run_batch.py
```

Wave 1 writes all three concurrently. The serial gate is re-imposed at **merge order**
(§6) and at **Wave 2**, which is where anything user-reachable lands.

---

## 2. Dependency graph

```
        +------------------------- WAVE 1 (parallel) --------------------------+
        |                                                                      |
Lane A  | B1 save_segments atomicity -> ISSUE-006 v10 migration -> O4 banner   |
        |                                                                      |
Lane B  | ISSUE-008 atomic publish + manifest + validate-on-hit + grandfather  |
        |                                                                      |
Lane C  | ISSUE-007 steps 0a/1/2/3 - Qt-FREE ONLY                              |
        |   separator-required invariant . analyse()/persist() split .         |
        |   progress+cancel hooks . environment preflight                      |
        +----------------------------------+-----------------------------------+
                                           |
                                 Integration gate (§6)
                                           |
        +------------------- WAVE 2 (serial, after gate) ----------------------+
        | ISSUE-007 steps 0c/4/5/6/7 - delete_track, AnalysisWorker QThread,   |
        | add-songs + progress dialogs, Home wiring, Analyze -> Correct labels |
        +----------------------------------------------------------------------+
```

Wave 2 is serial by necessity, not by policy: `AnalysisWorker` calls the `analyse`/
`persist` split (Lane C), persists through the transactional `save_segments` (Lane A),
and its Cancel button is only safe once stem publishing is atomic (Lane B). It is one
component depending on all three, so it cannot be split.

---

## 3. Lane charters

### Lane A — data integrity

| | |
|---|---|
| **Owns** | `guitar_helper/db/schema.py`, `guitar_helper/db/repository.py`, `guitar_helper/db/interfaces.py`, `guitar_helper/ui/state/editor_state.py`, `guitar_helper/ui/modes/output.py`, `tests/test_schema.py`, `tests/test_repository.py`, `tests/test_editor_state.py`, `tests/test_output_mode.py` |
| **Sources** | `docs/debug/ISSUE-006-preset-map-migration-divergence.md`, `mvp-readiness-review.md` §B1, `mvp-implementation-plan.md` §3 |
| **Note on `interfaces.py`** | The master plan §3.1 resolves B1's `EditorState.save` half by **adding** `apply_edits(updated, deleted_ids)` to `ISegmentEditor` rather than leaking the connection into the UI layer. That is an additive ABC change and is yours to make; the `Segment` / `Preset` dataclass *fields* stay frozen (§4). |
| **Design open** | A vs C (may a v10 reconcile overwrite a pre-v10 customisation?); where `user_modified` is set; `_relocate_unmapped_presets` policy for a surviving `ambient`; whether B1's transaction boundary lives in `SQLiteSegmentStore` or at each caller, given `EditorState.save` needs it too |
| **Pre-decided — do not relitigate** | Option **A** (A1 schema flag + A2 Output-mode banner) is the standing recommendation; the UNIQUE partial index on `pc_number >= 0` lands regardless; both migration traps (park-at-+1000, resolve duplicates before `CREATE UNIQUE INDEX`) are already reproduced and must be honoured |
| **Done when** | the v10 migration converges every legacy start version onto a fresh DB's `presets`; the 5 tests in ISSUE-006 §Test plan pass; `save_segments` and `EditorState.save` are atomic with a partial-failure test |
| **Hard rule** | Back up `library.db` before running v10 against it. Never DROP and recreate. |

### Lane B — stem cache integrity

| | |
|---|---|
| **Owns** | `guitar_helper/analysis/source_separator.py`, `tests/test_source_separator.py` |
| **Sources** | `docs/debug/ISSUE-008-stem-cache-poisoning.md`, `gui-analysis-pipeline-plan.md` §2.3, `mvp-implementation-plan.md` §4.2 |
| **Design open** | which manifest fields are obtainable without invoking a real separator; how `model_file_hash` is derived cheaply; grandfathering the 9 existing stems; whether S6 (move the cache out of OneDrive) is config-only |
| **Pre-decided** | **Must:** S1 atomic publish via `os.replace` · S2 completion manifest written last · S3 size + duration check on hit. **Should:** S5 cache-format version · S6 out of OneDrive. **Optional (skip unless free):** S4 sampled digest · S7 free-space precheck |
| **Done when** | tests 1–9 of ISSUE-008 §Test plan pass; a killed separation leaves no file at the cache path; existing stems are grandfathered with `"grandfathered": true` |
| **Hard rule** | Tests never invoke a real separator. The lazy `from audio_separator.separator import Separator` stays lazy. |
| **Hard rule — the live cache** | `stems/` holds **9 real stems, ~400 MB, irreplaceable without minutes of GPU-less separation each.** You may *add* manifest files beside them. You may **not** delete, move, truncate or rewrite any existing `*_guitar.wav`. Grandfathering is additive by definition. Do all destructive-path testing against a temp directory. A checksum inventory taken before the wave is held by the orchestrator and will be re-checked at the gate. |

### Lane C — GUI analysis groundwork (Qt-free)

| | |
|---|---|
| **Owns** | `guitar_helper/analysis/pipeline.py`, `guitar_helper/analysis/environment.py` (new), `guitar_helper/run_analysis.py`, `guitar_helper/run_batch.py`, `tests/test_pipeline.py`, `tests/test_environment.py` (new), `tests/test_run_batch.py`, **and `tests/test_ui_smoke.py:371` only** |
| **Sources** | `docs/debug/ISSUE-007-no-gui-analysis-path.md`, `docs/plans/gui-analysis-pipeline-plan.md` §0, §2.1–2.4, §4, steps 0a/1/2/3, `mvp-implementation-plan.md` §4.1 + §5 |
| **The one cross-boundary edit in this wave** | Making `separator` required breaks every bare `AnalysisPipeline(store)`. There are **10 such call sites**: 8 in `tests/test_pipeline.py` (yours) and **one in `tests/test_ui_smoke.py:371`**, which is otherwise Lane A's neighbourhood. You own that single line — add an explicit `NullSeparator()` and change nothing else in that file. If the line has moved, or the fix needs more than that one construction call, stop and report. |
| **Design open** | the `AnalysisResult` shape; `progress` / `should_cancel` callback signatures; cancel granularity between stages; how the preflight matrix is represented |
| **Pre-decided** | §0 invariant — analysis only ever sees a guitar stem; `NullSeparator` becomes test-only; `--no-separate` removed or renamed `--debug-full-mix`; the split follows decision D14, the same shape as M0 decode/attach |
| **Explicitly OUT of Wave 1** | anything under `guitar_helper/ui/`, `AnalysisWorker`, dialogs, `delete_track`. Those are Wave 2. |
| **Done when** | `AnalysisPipeline.run()` still passes the existing CLI tests; `analyse()` provably touches no store; `persist()` is the only writer; preflight is unit-tested with monkeypatched `find_spec` / `which` |

---

## 4. Frozen contracts (no lane may change these in Wave 1)

Parallelism is only safe while these hold. A lane that believes one must change **stops
and reports** rather than changing it.

| Interface | Frozen form | Who relies on it |
|---|---|---|
| `ISourceSeparator.separate_guitar(path, file_hash) -> Path` | unchanged | Lane B implements, Lane C calls |
| `ISegmentStore.save_segments(file_hash, segments) -> None` | signature unchanged; atomicity is an internal property | Lane A implements, Lane C calls from `persist()` |
| `ISegmentStore.save_track(...)` | unchanged | same |
| `Segment` / `Preset` / `Track` dataclass **fields** in `db/interfaces.py` | unchanged. Lane A may **add** `ISegmentEditor.apply_edits` (master plan §3.1); no lane may change an existing method's signature or a dataclass field | all three |
| `AnalysisPipeline.run(path, title, artist, k, discard_corrections)` | unchanged — master plan §5 keeps it precisely so both CLIs and their tests survive the split | Lane C |
| `tests/conftest.py` | **nobody edits it.** A lane needing a fixture defines it locally in its own test file | all three |

---

## 5. Stage to model routing (per CLAUDE.md)

Every lane runs the same four stages. The model is fixed by the *kind* of work, exactly as
the CLAUDE.md routing table specifies — not by the lane.

| Stage | Model | Agent | Output |
|---|---|---|---|
| 1. Design — resolve the lane's open decisions and architectural tradeoffs | `opus` | `issue-architect` | a decision note; no code |
| 2. Implement — code + tests against that note | `sonnet` | `issue-implementer` | working-tree edits, green tests |
| 3. Integrate — cross-lane review, full suite, ruff | `sonnet` | `issue-integrator` | verdict + fixes |
| 4. Document — issue status flips, SAVE_STATE, CLAUDE.md defect note | `haiku` | `issue-scribe` | doc edits only |

Stage 1 for all three lanes is spawned in a **single message** so the three Opus agents run
concurrently; stage 2 likewise. Stage 3 is one agent over all three lanes. Stage 4 is one
agent, last.

**Why stage 1 is Opus and not skipped:** each of the three issue documents ends with an open
choice, not an instruction — 006 has A/B/C pending, 008 has a Must/Should/Optional split whose
boundary depends on manifest feasibility, 007 has six open decisions. Those are "multi-step
design decisions" in the routing table's terms. The stage is narrow by design: resolve the
listed open decisions, do not re-derive the diagnosis.

---

## 6. Integration gate

Merge order re-imposes the published serial gate. Run in this order, stopping on failure:

1. **Lane A first.** `python -m pytest tests/test_schema.py tests/test_repository.py tests/test_output_mode.py`
2. **Lane B second.** `python -m pytest tests/test_source_separator.py`
3. **Lane C third.** `python -m pytest tests/test_pipeline.py tests/test_environment.py tests/test_run_batch.py`
4. **Whole suite.** `python -m pytest` — 370 tests were green in 27.5 s at baseline; the count must rise and the time must not blow out. No test invokes a real separator or a real analysis.
5. `python -m ruff check guitar_helper/`
6. **Stem inventory** — re-checksum `stems/*.wav` against the pre-wave snapshot. All 9 must be byte-identical; new `.json` manifests beside them are expected.
7. **Live DB migration**, only after 1–6 are green: confirm `library.db.bak-2026-08-18` exists and matches, then run `init_db` against `library.db`, and verify — 9 tracks / 123 segments / **123 corrections** intact, `presets` equal to a fresh v10 database, no duplicate `pc_number`. Report the rows verbatim.
8. One commit per lane, in merge order, so a bisect can separate them.

Wave 2 does not start until step 8 completes.

---

## 7. Conflict protocol

- A lane that needs to edit a file it does not own **stops and reports**; the orchestrator
  either reassigns the file or defers the change to integration.
- A lane that finds a defect outside its charter writes it up as a finding in its report;
  it does not fix it.
- Lint scope is per lane: each lane runs `ruff check` on its own files only, because the
  other lanes' edits are in flight in the same tree.
- No lane commits. The orchestrator commits at §6 step 8.

---

## 8. Reversibility

Established before launch, because three agents writing concurrently is exactly the
situation where "just undo it" needs to already be true.

| Restore point | What it recovers |
|---|---|
| Tag `pre-issue-006-008-wave` | The entire tree at a verified-green baseline — 370 tests, ruff clean. `git reset --hard pre-issue-006-008-wave` undoes the whole wave. |
| Branch `fix/issue-006-008-wave` | All wave work is isolated from `docs/mvp-readiness-review` and from `main`. Abandoning the branch costs nothing else. |
| `library.db.bak-2026-08-18` | The live library — 9 tracks, 123 segments, **123 manual corrections**. Restored by copying back over `library.db`. Covered by the `library.db.bak-*` gitignore rule. |
| Stem checksum inventory (orchestrator's scratchpad) | Proof that the 9 existing stems are byte-identical after the wave. Re-checked at the gate. |
| `git stash@{0}` | The stray `#sample rate` W291 edit on `audio_buffer.py`, stashed rather than discarded so the baseline could be green. Master plan §3.4 wants it gone permanently; that is Gate 1's call, not this wave's. |
| One commit per lane | Per §6 step 8. A single lane can be reverted without disturbing the other two — which matters most for Lane A, the only lane that writes to the live database. |

**The irreversible step, and where it sits.** The v10 migration mutates `library.db` in
place. It runs at gate step 6 — *after* the full suite is green — never inside a lane, and
never before the backup is verified. The 123 manual corrections it puts at risk are the
same data the June wipe destroyed and could not recover; treat that backup as the
precondition it is.
