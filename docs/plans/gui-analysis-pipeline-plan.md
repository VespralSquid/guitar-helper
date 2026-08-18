# Plan — GUI "Add songs" and analysis pipeline

**Addresses:** `docs/debug/ISSUE-007-no-gui-analysis-path.md`
**Status:** proposed, not started
**Written:** 2026-08-04

**Product decisions this implements (user, 2026-08-04):**

1. No songs and no database ship; the library is entirely user-supplied;
   therefore the GUI must be able to add and analyse songs. This is a release
   gate.
2. **Separation is mandatory. The full mix is never analysed.** Every song is
   stemmed first — on first ingestion, or when the user explicitly asks to
   re-analyse from scratch — and only the guitar stem is ever fed to the
   analyser. See §0.

---

## 0. Invariant — analysis only ever sees a guitar stem

This is a product rule, not a quality preference, and it reorganises several
decisions below.

**Interpretation used throughout this plan:** stem-then-analyse runs when a song
*enters the library* (or on an explicit re-analyse-from-scratch), and the result
persists in the database. Analysis does **not** re-run on every playback load —
that would cost minutes per song and defeat the purpose of storing segments at
all. If a literal re-analysis per load was intended, say so, because it changes
the architecture substantially.

### 0.1 Two code paths currently violate the invariant

**Violation 1 — full-mix analysis is the *default*.**

```python
# analysis/pipeline.py:43
self._separator = separator or NullSeparator()
```

Any caller that omits `separator` silently analyses the full mix and gets the
0.463-accuracy result from §2.4. That is a booby trap for exactly the new code
this plan adds: an `AnalysisWorker` that forgets to inject `AudioSeparator`
produces confidently wrong segments with no error anywhere.

*Fix:* make the separator **required** (no default), or default it to
`AudioSeparator`. `NullSeparator` stays in the codebase for tests only —
`tests/` must never be slowed by real separation — and should carry a docstring
saying it is test-only and must not be used for production analysis.

**Violation 2 — `--no-separate` is a user-facing flag on both CLIs.**
`run_analysis.py:37` and `run_batch.py:81` both expose it. Under the new rule
these should be removed, or renamed to something self-evidently diagnostic
(`--debug-full-mix`) with a printed warning that the results are not fit for
dispatch. Note `run_calibrate.py:14` already warns against `--no-separate`
precisely because of the stem/archetype coupling — the invariant generalises
that existing warning.

### 0.2 "Start over on a song" — scoped as remove-and-re-add

**Decision (user, 2026-08-04):** starting a song's analysis from scratch means
*remove the song from the library, then add it again*. No dedicated
re-analyse-from-scratch function is needed for MVP.

That is a smaller surface than a `force_separation` flag, but it depends on a
capability **that does not exist**:

```
$ grep -rn "delete_track\|remove_track\|DELETE FROM tracks" guitar_helper/
  (no matches)
```

There is no way to remove a track from the library — not in `ISegmentStore`, not
in the repository, not in the UI. `remove_from_playlist` only unlinks a track
from a playlist; the track, its segments and its corrections stay.

**Required for this plan: a `delete_track(file_hash)` capability**, on
`ITrackCatalog` (or a new `ITrackEditor` role, per the O3 segregation habit),
plus a Home right-click "Remove from library". It must clear `segments`,
`segments_calibration` and `playlist_tracks` rows for the hash — note
`playlist_tracks.file_hash` references `tracks` **without** `ON DELETE CASCADE`
(only `playlist_id` cascades), so a bare `DELETE FROM tracks` will hit a foreign
key error while any playlist still contains the song.

Two subtleties that make remove-and-re-add *not* automatically a fresh start:

1. **The stem cache is keyed by `file_hash`.** Re-adding the same file produces
   the same hash and therefore the same cached stem — so re-adding does **not**
   re-separate. Whether that is a problem depends on §2.3: once cached stems are
   validated on hit, a corrupt stem is regenerated automatically and reuse of a
   *valid* stem is exactly what you want (it saves minutes). So the combination
   of "no force flag" + "validated cache" is coherent — but only with §2.3 in
   place. **Without §2.3, remove-and-re-add cannot repair a poisoned stem, and
   the user has no escape hatch at all.** That makes step 0b a hard prerequisite
   for this decision, not an optional hardening.
2. **Removing a song deletes its manual corrections.** Those are the calibration
   ground truth, and the project has already lost a full set once.
   **Decided (user, 2026-08-04): warn with the count, then delete** — the
   confirmation must name the number of corrected segments that will be
   destroyed ("This will permanently delete 14 corrected segments"), and the
   whole removal must be transactional. A generic "Are you sure?" is not
   sufficient; the cost has to be visible at the moment of the decision.

   The cached stem is **kept** (decision 6), so remove-and-re-add resets labels
   without paying for separation again.

### 0.3 Consequence: separation becomes a hard dependency

If the full mix is never analysed, then a machine without the separation stack
**cannot ingest a single song** — the app's primary function is unavailable, not
degraded. This changes packaging from "ship the stack if convenient" to "the
product does not work without it" (see §7 decision 1).

---

## 1. Prerequisites — do not start before these land

GUI analysis makes `save_segments` reachable by ordinary users for the first
time. Two open defects turn that from a feature into a data-loss path:

| # | Defect | Why it blocks this work |
|---|---|---|
| 1 | `save_segments` is not transactional (`mvp-readiness-review.md` §B1) | A failed analysis write leaves a delete + partial insert in an open transaction, committed by the next unrelated write. Verified to destroy ground-truth segments. |
| 2 | ISSUE-006 — migrated DBs lack the `edge` preset row | An `edge` segment then raises `IntegrityError`, which is exactly the failure that triggers (1). |

**Gate: fix both first.** Everything below assumes analysis writes are atomic.

---

## 2. Design

### 2.1 The core move — split computation from persistence

`AnalysisPipeline.run()` currently reads the store at the start (corrections
guard) and writes it at the end. Neither can happen on a worker thread, because
the Qt main thread is the sole SQLite owner (`ARCHITECTURE.md` §4, decision D14).

This is structurally identical to the M0 decode/attach split, and takes the same
shape:

```python
@dataclass(frozen=True)
class AnalysisResult:
    file_hash: str
    duration_ms: int
    title: str | None
    artist: str | None
    segments: list[Segment]      # id=0, not yet persisted

class AnalysisPipeline:
    def analyse(self, path, *, title=None, artist=None, k=None,
                progress=None) -> AnalysisResult:
        """Pure computation. Touches no store, no Qt. Safe on a worker thread."""

    def persist(self, result: AnalysisResult, source_path: str) -> None:
        """Main thread only: save_track + save_segments, atomically."""

    def run(self, path, ...):
        """Unchanged public behaviour for the CLIs: guard -> analyse -> persist."""
```

`run()` keeps its current signature and semantics, so `run_analysis.py` and
`run_batch.py` need no changes and their tests keep passing. The corrections
guard stays in `run()` *and* is called separately by the GUI before dispatching
work — checking it up front avoids spending minutes on a track that will be
refused.

**Why not give the worker its own connection:** two writers on one SQLite file
introduces lock contention and a second source of truth for the corrections
invariant, and it breaks the single-owner rule that the whole threading model
rests on. The split costs less and preserves the invariant.

### 2.2 Progress reporting

Measured cost of a 189 s track with the stem already cached is 10.7 s
(`ISSUE-007`), and separation — excluded from that — is minutes. Progress must
therefore exist, but it can only be **stage-level**: `AudioSeparator.separate_guitar`
is a single blocking call into a third-party library with no progress hook.

```python
Stage = Literal["hashing", "separating", "decoding", "features",
                "segmenting", "classifying", "saving"]
progress: Callable[[Stage, int, int], None] | None   # stage, file_index, file_total
```

Honest reporting beats a fake percentage: show the stage name and *n of m*
files. `separating` should carry an explicit "this can take several minutes"
note, because it is the one stage that looks hung.

### 2.3 Cancellation — and the stem-cache corruption it exposes

Verified against the installed library: `Separator.separate(audio_file_path,
custom_output_names=None)` is the whole API. There is **no cancel, abort,
progress or callback hook** on the class. So **cancellation can only be honoured
between stages and between files**; a separation already in flight runs to
completion.

```python
should_cancel: Callable[[], bool] | None   # polled at each stage boundary
```

The UI must say so — "Finishing current song…" after Cancel is pressed, not a
frozen dialog.

**But cancellation surfaces a pre-existing defect that must be fixed first.**
`AudioSeparator.separate_guitar` decides cache validity by existence alone:

```python
cached = self._cache_dir / f"{file_hash}_guitar.wav"
if cached.exists():
    return cached
```

If a separation is interrupted — user cancels, app is force-quit, machine
sleeps, power fails — a partially written `.wav` is left in the stem cache and is
treated as a valid hit **forever after**. Reproduced by truncating a real stem to
33% and re-running:

```
simulated interrupted separation: 46,493,778 -> 15,497,926 bytes (33%)
separate_guitar() returned the truncated file as a CACHE HIT: True
load_mono() SUCCEEDED silently -> 87.9s of audio (expected ~264s)
```

Every layer accepts it. The downstream effect is silent corruption, not an
error: the feature matrix covers 88 s while `find_boundaries` is handed the
*authoritative* `duration_ms` of 264 s, so all detected boundaries land inside
the first third and the final segment is stretched across the remaining ~176 s
as a single mislabelled block. The user sees a plausible-looking segment list
that is wrong, and re-analysing does not fix it because the poisoned stem is
still a cache hit.

**This is now tracked as its own defect: `docs/debug/ISSUE-008-stem-cache-poisoning.md`**,
which enumerates the full failure taxonomy (13 cases across 4 classes, not just
cancel and termination), measures the candidate validation strategies, and
recommends a layered fix. Headline: atomic publish via a temp dir +
`os.replace`, a completion manifest written last carrying provenance, and an
O(1) size check on cache hit.

Cancellation cannot ship before ISSUE-008 is fixed — adding a Cancel button
converts its worst case from theoretical into a first-class user action.

Add a "Clear stem cache" action in Preferences as the manual escape hatch.

### 2.4 Environment preflight

Check *before* starting, and report in one dialog rather than failing mid-run:

| Check | Method | On failure |
|---|---|---|
| Separation stack | `importlib.util.find_spec("audio_separator")` | **Block ingestion.** Per §0 there is no proceed-anyway option; explain what is missing and how to install it |
| **htdemucs_6s weights present** | model file exists under the app's `model_dir` | Weights are bundled (decision 5), so absence means a damaged install — report that, do not silently fall back to a download |
| ffmpeg | `shutil.which("ffmpeg")` | Warn that `.m4a`/`.mp3` will fail; native formats still work |
| Selected files readable / supported suffix | `Path.exists()`, suffix set | Exclude with a per-file reason |
| Writable stem cache + DB dir | attempt a temp write | Fail before any expensive work |

**The model-weights check is not optional, and it dents a headline claim.**

There are **two unrelated "models"** in this product and they are easy to
conflate:

| | `archetypes.json` | `htdemucs_6s` |
|---|---|---|
| What it is | Tone-classifier calibration — per-tone mean feature vectors | Third-party pretrained neural network for **source separation** |
| Job | Decide *which tone* a stem's features match | Split a mixed recording into instrument stems |
| Size | **3,153 bytes** (5 tones × 24 floats = 120 numbers) | **~52 MB** (`5c90dfd2-34c22ccb.th`) |
| Origin | Derived from the user's own 129 labelled segments | Fixed, pretrained, from the Demucs project |
| Adapts? | **Yes** — `run_calibrate` rewrites it as the user labels more | **No** — never changes, never calibrated |
| Ships in repo? | **Yes**, tracked in git | **No** |

Pipeline order makes the dependency direction obvious:

```
full mix --[ htdemucs_6s : 52 MB, third-party, fixed ]--> guitar stem
guitar stem --> FeatureExtractor --> 24 numbers
24 numbers --[ archetypes.json : 3 KB, user's own, adapts ]--> tone label
```

So `archetypes.json` is **useless without `htdemucs_6s` running first** — it is
calibrated on stem features, and there is no stem without the separation
network. Shipping the calibration does not remove the need for the separator.

Two further practicalities:

- **The 52 MB weights are separate from the ~2 GB dependency stack.** The 2 GB
  is torch + onnxruntime — the code that *runs* the network. The 52 MB is the
  network itself. Both are required; only the weights are currently fetched at
  runtime.
- **The weights currently live in a temp directory.** Verified on this machine:
  `C:/tmp/audio-separator-models/5c90dfd2-34c22ccb.th`, downloaded 2026-06-19 on
  the first separation. `audio_separator` defaults
  `model_file_dir="/tmp/audio-separator-models/"`, and although `AppConfig` has
  a `model_dir` field, nothing sets it by default. A temp directory is not a
  safe cache location for a shipped product — disk-cleanup tools remove it, and
  the next separation then silently re-downloads or fails offline. Under the
  `%LOCALAPPDATA%\GuitarHelper` decision this must move there explicitly.

`CLAUDE.md` and Report v0.4 both describe this as a "**fully offline** Python
desktop app" — true of playback, MIDI and correction, but **not** of the first
separation on a fresh machine unless the weights are bundled. With separation
now mandatory (§0), that is the first thing every user does.

**The separation warning must be specific, and the size of the penalty is now
measured rather than assumed.** Classifying all 123 human-labelled segments with
the shipped (stem-calibrated) `archetypes.json`, from stem features versus
full-mix features:

| source | accuracy | macro-F1 |
|---|---|---|
| separated stem | **0.821** | **0.783** |
| full mix (`NullSeparator`) | **0.463** | **0.324** |

Per-tone F1, and where the mix sends each tone instead:

| tone | support | stem | mix | Δ |
|---|---|---|---|---|
| clean | 33 | 0.70 | 0.58 | −0.12 |
| edge | 8 | 0.56 | 0.14 | −0.42 |
| overdrive | 6 | 0.62 | **0.00** | −0.62 |
| crunch | 29 | 0.89 | **0.13** | −0.76 |
| metal | 28 | 0.95 | 0.53 | −0.41 |
| other | 19 | 0.97 | 0.55 | −0.42 |

*Resubstitution scores* — the segments being classified are the ones that
built the archetypes, so both columns are optimistically biased. The
comparison holds because the only variable is stem vs full mix; the honest
generalisation figure is the LOOCV macro-F1 of ~0.69 in EXP-001.

The failure mode is systematic, not noisy: **the full mix collapses almost
everything into `metal`.** 27 of 29 `crunch` segments are predicted `metal`, all
6 `overdrive` segments are wrong, and 12 of 33 `clean` segments read as `metal`.
Drums and bass add broadband energy that raises flatness, ZCR and centroid —
precisely the distortion signature the archetypes key on.

**A correction to an earlier characterisation of this flag:** the failure is
*not* that `edge` and `crunch` become inseparable (that was ISSUE-003, a
different problem in stem space). Note the trap in the raw numbers — the
edge/crunch confusion rate actually reads **0.000** on the full mix, which looks
like an improvement. It is not: the two never get confused *with each other*
because both have already collapsed into `metal`.

**Under the §0 invariant these numbers are no longer a warning to display —
they are the justification for refusing full-mix analysis outright.** The table
stays here as the evidence base for that rule, and as the answer to anyone who
later proposes a "lite" build without the separation stack: such a build would
label most of a rock song `metal` and hold one preset for its duration, which is
the exact failure the product exists to prevent.

### 2.5 Duplicate and corrections handling

Hash first (0.05 s), then decide — before any expensive work:

| Condition | Behaviour |
|---|---|
| Hash not in DB | Analyse. |
| Hash present, no manual corrections | Skip by default; a "Re-analyse existing" checkbox in the dialog overrides. |
| Hash present **with** manual corrections | **Always skip**, and report it. Never offer `discard_corrections` from the GUI — the CLI flag exists for deliberate use and this is the data whose loss the project has already suffered once. |

Mirrors `run_batch`'s SKIP semantics so CLI and GUI agree.

### 2.6 Files stay where they are

No copying into a managed library folder. `source_path` is recorded and
`file_locator.locate()` re-finds moved files by content hash — the existing
model works and needs no change. The stem cache stays keyed by hash under
`stems/`, which under the packaging decision becomes
`%LOCALAPPDATA%\GuitarHelper\stems`.

---

## 3. Components

| File | Status | Responsibility |
|---|---|---|
| `analysis/pipeline.py` | modify | Split into `analyse()` / `persist()` / `run()`; add `progress` + `should_cancel` |
| `analysis/environment.py` | **new** | Qt-free preflight: separation stack, ffmpeg, per-file validation. Returns a report dataclass |
| `ui/analysis_worker.py` | **new** | `QThread` running `analyse()` over a file list; signals `progress`, `fileDone(AnalysisResult)`, `fileFailed(path, msg)`, `finished` |
| `ui/dialogs/add_songs.py` | **new** | File/folder picker + options (recursive, re-analyse existing, target playlist) + preflight report |
| `ui/dialogs/analysis_progress.py` | **new** | Stage + n-of-m display, Cancel, per-file result list |
| `ui/modes/home.py` | modify | "Add songs…" button; right-click "Add songs to this playlist"; rename the existing **Analyze** button |
| `ui/main_window.py` | modify | Own the worker lifecycle; persist each result on the main thread; refresh Home |

### 3.1 Rename the existing "Analyze" button

It does not analyse — it opens already-analysed tracks for correction
(ISSUE-007). With a real analyse action arriving, two controls would be
irreconcilably confusing. Rename to **"Correct labels"** (or "Review segments").
This also fixes the mislabelling as a defect in its own right.

---

## 4. Threading contract

Extends `ARCHITECTURE.md` §4 with one row:

| Thread | Owns | May touch | Must never |
|---|---|---|---|
| `AnalysisWorker` (QThread) | one `AnalysisPipeline` | audio files, stem cache, librosa/torch | touch SQLite, Qt widgets, or playback objects |

Persistence happens in the main-thread slot for `fileDone`, exactly as `attach()`
handles `decoded`.

**Concurrency:** one analysis run at a time, guarded like `_load_worker is not
None`. A batch is sequential within the single worker — parallel separation
would contend for CPU with playback and risks the ISSUE-005 class of problem.

**Interaction with playback:** analysis is CPU-heavy and *will* compete with the
audio callback for the GIL. Two mitigations, in order of preference:

1. Do the numpy/librosa work in a worker where the heavy calls release the GIL
   (librosa and torch both do for their native sections) — this is already true
   of the design and is why a `QThread` is acceptable at all.
2. If live testing still shows dropouts, offer "pause playback while analysing"
   rather than pretending it is free.

**This must be verified by ear before release** (the ISSUE-005 lesson: a
diagnosis from code inspection is a hypothesis, not a cause).

---

## 5. Implementation steps

| # | Step | Deliverable |
|---|---|---|
| 0 | **Prereqs** — B1 transaction fix + ISSUE-006 migration | §1 gate cleared |
| 0a | **Enforce the §0 invariant** — separator becomes required (or defaults to `AudioSeparator`); `NullSeparator` marked test-only; `--no-separate` removed or renamed `--debug-full-mix` | Test: constructing `AnalysisPipeline` without a separator cannot silently analyse a full mix |
| 0b | **Atomic stem writes + cache validation** (§2.3) | Test: a truncated cached stem is rejected as a miss, not returned as a hit |
| 0c | **`delete_track(file_hash)`** (§0.2) + Home "Remove from library" with a corrections-count confirmation | Tests: removal clears segments/calibration/playlist rows transactionally; a track in a playlist can be removed without an FK error |
| 1 | Split `AnalysisPipeline` into `analyse` / `persist` / `run` | Existing CLI tests still green; new unit tests for the split |
| 2 | Add `progress` + `should_cancel` hooks | Unit test asserting stage order and mid-run cancel |
| 3 | `analysis/environment.py` preflight | Qt-free unit tests with monkeypatched `find_spec` / `which` |
| 4 | `AnalysisWorker` QThread | pytest-qt test with a stub pipeline (never runs real analysis) |
| 5 | Add-songs dialog + progress dialog | pytest-qt construction and option-plumbing tests |
| 6 | Home wiring: Add button, right-click, **rename Analyze → Correct labels** | Smoke test: add → analyse (stubbed) → track appears in Home |
| 7 | Main-window wiring: persist on `fileDone`, `home.refresh()` on finish | Also closes review finding **H4** (stale progress counts) |
| 8 | Live verification on the real rig | Real multi-song add (always separated, per §0); a forced re-analyse-from-scratch; a cancel mid-separation followed by a clean re-run; playback-during-analysis checked by ear |

Steps 1–3 are Qt-free and independently testable — do them first and in that
order.

---

## 6. Test strategy

**Never run real analysis in tests.** A single track is ~11 s without separation
and minutes with it; the suite currently runs in 40 s and must stay usable.

- **Unit (Qt-free):** the `analyse`/`persist` split; progress stage sequence;
  cancel between stages; preflight matrix (stack present/absent × ffmpeg
  present/absent); duplicate/corrections decision table from §2.5.
- **Qt (pytest-qt):** inject a stub pipeline whose `analyse()` returns a
  canned `AnalysisResult` immediately. Assert the worker emits in order, that
  `persist` runs on the main thread, that Home refreshes, and that the Add
  button is disabled during a run.
- **Regression:** one test asserting the corrections guard still refuses a
  corrected track through the *GUI* path, mirroring
  `test_reanalysis_guard_fires_after_gui_edit_and_save`.
- **First-run:** a test that builds the app against an empty temp root and
  asserts the add-songs action is reachable and enabled — the check whose
  absence allowed ISSUE-007.

---

## 7. Open decisions

| # | Decision | Options | Recommendation |
|---|---|---|---|
| 1 | Ship the separation stack (~2 GB torch + onnxruntime)? | (a) bundle (b) ~~never — full-mix only~~ **eliminated by §0** (c) first-run download | **(a) bundle — follows from decision 5.** Bundling 52 MB of weights for offline use is pointless if the code that runs them still downloads. §0 makes separation mandatory, so a first-run download would mean a new user cannot add a single song without internet. The installer is large; that is the cost of an offline product whose core operation is a neural network. **Verify the real frozen size early** — torch's PyInstaller footprint is the biggest unknown in the packaging estimate |
| 2 | Bundle ffmpeg? | (a) bundle (b) detect + link to install | **(a)** if licensing permits — `.m4a` is the stated primary format, and asking a guitarist to install ffmpeg loses most of them |
| 3 | Pause playback during analysis? | (a) always (b) never (c) offer if dropouts are heard | **(c)** — decide from the step-8 listening test, not from speculation |
| 4 | Allow re-analysis of corrected tracks from the GUI? | (a) never (b) behind a confirmation | **(a)** for MVP — the CLI flag remains the deliberate escape hatch |
| 5 | Bundle the htdemucs_6s weights (~52 MB)? | — | **RESOLVED (user, 2026-08-04): bundle in the installer.** 52 MB is small next to the ~2 GB torch stack that ships anyway; first analysis then works with no network step and "fully offline" becomes true. Weights must live under `%LOCALAPPDATA%\GuitarHelper`, not the library's temp-dir default |
| 6 | Does "Remove from library" delete the cached stem? | (a) keep (b) delete | **(a) keep** — my call, override if you disagree. With corrections deleted on removal (decision 7), keeping the stem means remove-and-re-add is a genuine fresh start on labels while skipping the minutes-long separation. Stems self-validate after step 0b, so a kept stem is never stale. Bulk stem eviction stays a separate QOL item (~46 MB each, 400 MB for 9 songs, nothing evicts today) |
| 7 | Removing a song and its manual corrections | — | **RESOLVED (user, 2026-08-04): warn with the count, then delete.** Confirmation must name the number of corrected segments that will be destroyed, and the delete must be transactional |

Decision 1 is the one that most affects packaging scope and should be settled
before step 5.

---

## 8. Definition of done

A user installs the app on a clean machine, opens it to an empty library, clicks
**Add songs…**, selects a folder of their own music, watches per-song progress,
and ends with an analysed, playable, correctable library — without a terminal,
without Python, and without any data shipped by us.
