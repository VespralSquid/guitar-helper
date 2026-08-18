# Orchestration — ISSUE-006 / 007 / 008 parallel fix wave

**Created:** 2026-08-18
**Scope:** the three open MVP blockers (ISSUE-006, ISSUE-007, ISSUE-008) plus
`mvp-readiness-review.md` §B1, which is a hard prerequisite of both 006 and 007.
**Launch with:** `/issue-wave` (see `.claude/commands/issue-wave.md`).

---

## 1. Why these three can run in parallel at all

The published sequencing (`mvp-readiness-review.md` §9, `gui-analysis-pipeline-plan.md` §1)
is strictly serial: B1 → 006 → 008 → 007. That ordering is a **ship gate, not a write
gate**. It exists because GUI analysis makes `save_segments` reachable by ordinary users,
so 007 must not *reach a user* before 006 and B1 are sound.

Nothing in it requires the code to be *written* serially, because the three fixes touch
disjoint files and communicate only across interfaces that none of them change:

```
Lane A  db/schema.py, db/repository.py, ui/modes/output.py
Lane B  analysis/source_separator.py
Lane C  analysis/pipeline.py, analysis/environment.py (new)
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
| **Owns** | `guitar_helper/db/schema.py`, `guitar_helper/db/repository.py`, `guitar_helper/ui/modes/output.py`, `tests/test_schema.py`, `tests/test_repository.py`, `tests/test_output_mode.py` |
| **Sources** | `docs/debug/ISSUE-006-preset-map-migration-divergence.md`, `mvp-readiness-review.md` §B1 |
| **Design open** | A vs C (may a v10 reconcile overwrite a pre-v10 customisation?); where `user_modified` is set; `_relocate_unmapped_presets` policy for a surviving `ambient`; whether B1's transaction boundary lives in `SQLiteSegmentStore` or at each caller, given `EditorState.save` needs it too |
| **Pre-decided — do not relitigate** | Option **A** (A1 schema flag + A2 Output-mode banner) is the standing recommendation; the UNIQUE partial index on `pc_number >= 0` lands regardless; both migration traps (park-at-+1000, resolve duplicates before `CREATE UNIQUE INDEX`) are already reproduced and must be honoured |
| **Done when** | the v10 migration converges every legacy start version onto a fresh DB's `presets`; the 5 tests in ISSUE-006 §Test plan pass; `save_segments` and `EditorState.save` are atomic with a partial-failure test |
| **Hard rule** | Back up `library.db` before running v10 against it. Never DROP and recreate. |

### Lane B — stem cache integrity

| | |
|---|---|
| **Owns** | `guitar_helper/analysis/source_separator.py`, `tests/test_source_separator.py` |
| **Sources** | `docs/debug/ISSUE-008-stem-cache-poisoning.md`, `gui-analysis-pipeline-plan.md` §2.3 |
| **Design open** | which manifest fields are obtainable without invoking a real separator; how `model_file_hash` is derived cheaply; grandfathering the 9 existing stems; whether S6 (move the cache out of OneDrive) is config-only |
| **Pre-decided** | **Must:** S1 atomic publish via `os.replace` · S2 completion manifest written last · S3 size + duration check on hit. **Should:** S5 cache-format version · S6 out of OneDrive. **Optional (skip unless free):** S4 sampled digest · S7 free-space precheck |
| **Done when** | tests 1–9 of ISSUE-008 §Test plan pass; a killed separation leaves no file at the cache path; existing stems are grandfathered with `"grandfathered": true` |
| **Hard rule** | Tests never invoke a real separator. The lazy `from audio_separator.separator import Separator` stays lazy. |

### Lane C — GUI analysis groundwork (Qt-free)

| | |
|---|---|
| **Owns** | `guitar_helper/analysis/pipeline.py`, `guitar_helper/analysis/environment.py` (new), `run_analysis.py`, `run_batch.py`, `tests/test_pipeline.py`, `tests/test_environment.py` (new), `tests/test_run_batch.py` |
| **Sources** | `docs/debug/ISSUE-007-no-gui-analysis-path.md`, `docs/plans/gui-analysis-pipeline-plan.md` §0, §2.1–2.4, §4, steps 0a/1/2/3 |
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
| `Segment` / `Preset` dataclasses in `db/interfaces.py` | unchanged | all three |
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
4. **Whole suite.** `python -m pytest` — must stay near its current ~40 s; no test invokes a real separator or a real analysis.
5. `python -m ruff check guitar_helper/`
6. **Live DB migration**, only after 1–5 are green: back up `library.db`, run `init_db` against it, confirm `presets` matches the fresh v10 layout and no duplicate `pc_number` exists.
7. One commit per lane, in merge order, so a bisect can separate them.

Wave 2 does not start until step 7 completes.

---

## 7. Conflict protocol

- A lane that needs to edit a file it does not own **stops and reports**; the orchestrator
  either reassigns the file or defers the change to integration.
- A lane that finds a defect outside its charter writes it up as a finding in its report;
  it does not fix it.
- Lint scope is per lane: each lane runs `ruff check` on its own files only, because the
  other lanes' edits are in flight in the same tree.
- No lane commits. The orchestrator commits at §6 step 7.
