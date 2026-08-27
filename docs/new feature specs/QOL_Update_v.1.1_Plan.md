# Update 1.1
    This plan outlines feature updates to be implemented after the MVP is packaged and ready for release. 

## UX
    - add help tab to redirect to repo or documentation(to be created later, such as README and other helpful docs)
    - add right click menus to playlist content window with options to add songs, either from file or from a different playlist
    - Add preferences tab to top bar to allow configuration of playback settings, such as an option to count into songs by a user selected number of bars or the ability to hear a click track
    - Add ability to control playback via keyboard input, such as space for pause and play, arrow keys to seek 5 seconds forward or backward.

## UI
    - Have the analysis loading bar represent actual progress: currently it stays at 0% until analysis concludes. Have the loading bar reflect the stage of the process and the progress the way the cli does, stemming progress and then analysis — see "Analysis progress reporting" below.

---

# Detailed specs

## Analysis progress reporting

### What is actually happening today

Two corrections to the one-liner above, both of which change what the work is:

**1. Stage reporting already exists and already works.** `Stage` (`analysis/pipeline.py:19`),
`ProgressFn`, the `_enter()` calls at six stage boundaries, `AnalysisWorker.progress`
(a 4-tuple carrying the stage), and `_STAGE_TEXT` in the progress dialog are all in
place. The dialog's `stage_label` *does* update live — "Separating the guitar stem -
this can take several minutes...", then "Extracting features...", and so on.

The **bar** is what is stuck, and it is stuck because it is scaled in *files*, not
stages:

```python
# ui/dialogs/analysis_progress.py:87-88
self.progress_bar.setRange(0, file_total)
self.progress_bar.setValue(max(0, file_index - 1))
```

With one song selected that is `setRange(0, 1)` / `setValue(0)` — literally 0% until
the file completes, then 100%. The reported symptom is exactly this line, not missing
plumbing.

**2. The CLI does not emit stage progress either.** `run_analysis.py` and
`run_batch.py` call `pipeline.run()`, which passes no `progress` callback at all. The
separation progress visible in a terminal is **audio-separator's own tqdm bar and
logging on stderr** — `Separator(**kwargs)` is constructed at
`analysis/source_separator.py:356` without a `log_level`, so its default INFO logging
and tqdm go straight to the console. There is no console in the GUI, which is why it
vanishes.

So "the way the cli does it" cannot be achieved by adding our own callback. It means
either capturing third-party output or estimating. See below.

### Why the current design is the way it is

`ui/dialogs/analysis_progress.py:3-5` documents this as a deliberate choice:

> Progress is stage-level and honest rather than a fake percentage: the separator is
> a single blocking third-party call with no progress hook, and it is also the only
> stage that takes minutes.

That reasoning is sound and this spec does not discard it. A naive
"7 stages, 1/7th each" bar would be *worse*: it would jump to 28% in about a second,
then freeze there for several minutes during separation, which reads as a hang. Any
implementation has to solve the separation stage specifically or it makes things
worse, not better.

### Design

**Two bars, not one.** The current single bar conflates batch position with per-file
progress; splitting them is what makes both honest.

- **Overall** — files completed, `0..file_total`. This is the existing bar, kept as-is.
- **Current file** — stage-weighted, `0..1000` (fine-grained so separation can move
  smoothly inside its slice).

**Stage weights.** Equal weighting is wrong because the costs differ by two orders of
magnitude. Approximate real costs, to be measured during implementation:

| Stage | Rough cost | Weight |
|---|---|---|
| `HASHING` | ~0.05 s / 44 MB | 1% |
| `SEPARATING` | minutes | 85% |
| `DECODING` | ~1 s | 3% |
| `FEATURES` | seconds | 6% |
| `SEGMENTING` | ~1 s | 3% |
| `CLASSIFYING` | <1 s | 1% |
| `SAVING` | ~0 | 1% |

Weights belong next to `ANALYSE_STAGES` in `pipeline.py` so the ordering and the
weighting cannot drift apart.

**Separation sub-progress** is the actual problem. Three options, in preference order:

1. **Duration-scaled estimate (recommended).** `duration_ms` is already known from
   `_loader.load()` *before* separation starts. Separation time is near-linear in
   audio length on fixed hardware, so drive a `QTimer` across the separation slice
   using a stored throughput constant (separation-seconds per audio-second). Keep an
   EMA of observed throughput in the existing `settings` table so it self-calibrates
   to the user's machine after a few songs; seed it with a measured default.
   - Must clamp at ~95% and only snap to the top of the slice when
     `separate_guitar()` actually returns. A bar that hits 100% and sits there is the
     same failure as one that sits at 0%.
   - Label it as an estimate in the stage text.
2. **Indeterminate bar during separation** (`setRange(0, 0)`, Qt's busy animation).
   Zero risk, honest, communicates "working, duration unknown". Good fallback and a
   reasonable first increment on its own.
3. **Capture audio-separator's real progress.** Closest to the CLI, and the only
   genuinely accurate option. Needs a spike: pass a configured logger via
   `Separator(log_level=...)` and attach a handler, or redirect stderr and parse
   tqdm. Fragile — it couples us to a third party's output format across versions.
   Only pursue if a spike shows the format is stable and parseable.

Recommendation: ship (1) with (2) as the fallback whenever no throughput estimate is
available yet (first run, or a wildly off previous estimate). Treat (3) as optional.

### Constraints

- `AnalysisWorker.progress` currently emits `(index, total, filename, stage)`. Adding
  a sub-progress fraction means either a 5th field or a second signal — prefer a
  second signal so existing connections and `test_analysis_worker.py` stay valid.
- Nothing new may run on the audio callback path; this is all worker-thread and Qt
  main-thread only.
- **Cancel semantics must not change.** Cancel is honoured at stage and file
  boundaries only, and a separation in flight always runs to completion
  (`pipeline.analyse()` docstring). A smoothly-moving separation bar must not imply
  otherwise — keep the existing "Finishing current song..." text and the disabled
  Cancel button.
- The `settings` table is the right home for the throughput EMA; do not add a schema
  table for it.

### Acceptance

- Single-file analysis shows continuous movement rather than 0% → 100%.
- Multi-file analysis shows both batch position and within-file progress.
- The separation slice never reaches 100% before `separate_guitar()` returns.
- A first-ever run (no stored throughput) still shows something sensible.
- `test_analysis_progress_dialog.py` and `test_analysis_worker.py` extended; existing
  cancel-behaviour tests unchanged and still passing.
