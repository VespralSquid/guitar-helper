# Orchestrator reconciliation — stage 1 → stage 2

The three architects worked concurrently and could not see each other's notes. This
document resolves everything that crosses a lane boundary. **It overrides the individual
notes where they disagree.** Implementers read their own note plus this file.

Checked and found clean: no note requires changing a frozen contract (orchestration §4);
Lane A and Lane C agree on the transaction boundary — Lane A owns it inside the store,
Lane C's `persist()` simply calls `save_segments` and inherits the atomicity.

---

## R1. `separate_guitar` gains an exception. Lane C must expect it.

**The gap.** Lane B D8 introduces `SeparationError(RuntimeError)`, raised when separation
produces no usable output, when post-write validation rejects the file, on insufficient
disk space, or when publish fails. Lane B found (F2) that `Separator.separate()` swallows
every exception internally and that `separate_guitar` today returns `cached`
unconditionally — so a failed separation currently returns a path to a file that does not
exist, and surfaces two layers later as a librosa error.

Lane C's `analyse()` calls `separate_guitar` and its note was written without knowledge of
this, because both notes were produced in parallel.

**Resolution.** The signature stays frozen, so this is additive, not a contract change.

- **Lane C:** do **not** catch `SeparationError` in Wave 1. Let it propagate out of
  `analyse()`. Name it in `analyse()`'s docstring as a documented raise, importing it from
  `analysis.source_separator` (an intra-tier import, no cycle). Per-file error handling
  belongs to the Wave 2 worker, which is the layer that can decide to continue a batch.
- **Lane B:** the class must exist and be importable by the time Lane C's tests run. If you
  rename it, report immediately — Lane C imports it by name.

## R2. `NullSeparator`'s docstring is Lane B's edit, not Lane C's.

Lane C D-item 1 correctly refused to edit `analysis/source_separator.py`. **Lane B makes
this change**, using Lane C's suggested text:

> Passthrough — returns the input unchanged. TEST AND FIXTURE USE ONLY: analysing a full
> mix scores 0.463 accuracy against 0.821 for a separated stem (ISSUE-007 §2.4). Never
> construct this for a library the user will play from.

## R3. `MockStore` must stay a real ABC subclass. This is load-bearing for Lane A.

Lane A D7 makes both new ABC methods (`ISegmentEditor.apply_edits`,
`IPresetStore.reset_presets_to_defaults`) **concrete** rather than abstract, because
`tests/test_pipeline.py:14` defines `class MockStore(ISegmentStore)` — a real subclass that
an abstract addition would make uninstantiable. Lane A cannot edit that file, so it relies
on the concrete default instead.

**Lane C owns `tests/test_pipeline.py` and is rewriting parts of it.** Keep `MockStore` a
genuine `ISegmentStore` subclass. Do not replace it with a `Mock`, a `Protocol`, a
`SimpleNamespace` or a duck-typed stand-in. It is the canary proving no new abstract method
was introduced; converting it would silently disarm Lane A's safeguard.

If your design needs a non-subclass double, add one *alongside* `MockStore` rather than
replacing it.

## R4. Approved deviations from written sketches

Each is evidence-backed and each was flagged rather than made silently. All three stand.

| Lane | Deviation | Standing |
|---|---|---|
| A (D4) | `apply_edits(file_hash, updated, deleted_ids)` — adds `file_hash` to the master plan §3.1 two-argument sketch, because the sketch cannot call `ensure_calibration_copy` when `updated` is empty, and deriving the hash from `updated[0]` would depend on an incidental property of `merge_run` | **Approved.** New method, no frozen signature broken. The master plan's sketch is a sketch. |
| B (D5, D6) | S4 sampled digest promoted Optional → **Must**; S7 free-space precheck included in minimal form | **Approved.** ISSUE-008's test 2 assumed `stem_bytes` catches zero-fill; measurement shows a genuine zero-fill preserves byte count *and* duration exactly, so size and duration together are blind to the failure mode singled out as the dangerous one. 2.75 ms per hit is not worth arguing about. |
| B (D2) | Model identity derived from the model directory rather than hashing 52 MB of weights per hit | **Approved.** Same provenance guarantee at 0.464 ms instead of 50 ms. |
| C (D11) | `--no-separate` **removed** rather than renamed `--debug-full-mix` | **Approved.** The master plan §4.1 offers either. Removal is the stronger reading of the mandatory-separation invariant. Note it leaves `run_calibrate.py:14` referring to a flag that no longer exists — a finding for Wave 2, not a Lane C edit. |

## R5. `tests/test_ui_smoke.py` — two lines, not one

Lane C's charter granted line 371 only. The real change needs the construction call **plus**
a function-local `NullSeparator` import. **Approved**, on Lane C's own condition: both lines
stay inside the test function body, leaving the module-level import block — where a Lane A
collision would occur — untouched. Nothing else in that file may change.

---

## Unchanged and still binding

- No lane commits. The orchestrator commits per lane at the integration gate.
- No lane edits `tests/conftest.py`.
- Lane B may not delete, move, truncate or rewrite any existing `stems/*_guitar.wav`; a
  checksum inventory is re-verified at the gate.
- No lane runs a real analysis or a real separation, or touches `library.db`.
