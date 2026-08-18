# Guitar Performance Assistant
## Project Design Report — Version 0.4
### As-Built Architecture (Phases 1–4)

**Version:** 0.4 — As-Built Reconciliation
**Date:** 2026-08-04
**Author:** Aryan
**Supersedes:** v0.3 (`Guitar_Performance_Assistant_Report_v0.3.md`, 2026-06-11)

---

## 0. What changed in v0.4, and why this version exists

v0.3 was a *design* document written before Tier 2 and Tier 3 were built. Three
phases of implementation later, it described components that were never built,
a tone taxonomy that was replaced, and a database schema five versions behind.
Because it is the document a new reader opens first, that drift was the single
largest documentation risk in the project.

v0.4 is a *reconciliation*: it describes the system that exists. Design intent
that was tried and abandoned is recorded as such rather than deleted, because
the reasons are usually more useful than the conclusions.

| Area | v0.3 said | v0.4 (as built) | Cause of change |
|---|---|---|---|
| Tone taxonomy | `clean / crunch / heavy / ambient / other` | `clean / edge / overdrive / crunch / metal / other` | `heavy`→`metal` rename; `edge` and `overdrive` added to fill the gain ramp; `ambient` deferred for lack of calibration data (schema v4) |
| Analysis input | Full mix | Isolated guitar stem (htdemucs_6s) | ISSUE-001/003: full-mix features were dominated by drums and bass |
| Feature normalisation | One scheme | Two — per-song for segmentation, fixed-range for classification | ISSUE-002: per-song normalisation destroys the absolute spectral character classification depends on |
| `SegmentLookup` | SQL query per lookup | In-memory bisect over a snapshot taken at construction | sqlite3 connections are not thread-shareable; a per-tick query has no place in the dispatch loop |
| Waveform / spectrum | pyqtgraph `WaveformView` + `SpectrumAnalyzer` | `SegmentTimeline` (custom `paintEvent`); spectrum deferred | ISSUE-005: pyqtgraph's 20 Hz scene repaint starved the audio callback of the GIL |
| `IRenderer` abstraction | Central Tier 3 seam | Never built | The renderers it was to abstract were removed before the seam was needed |
| Segment correction | CLI only | CLI + full GUI editor (Phase 4 O3) | Correction is the highest-volume user activity; CLI was the bottleneck |
| Amp host | Ableton Live 12 Lite + Neural DSP VST3 | Cantabile + Neural DSP **VST2 or standalone** | ISSUE-004: the VST3 format does not deliver raw Program Change to hosted plugins |
| Lyrics | `LyricsParser` + `LyricsDisplay` | Not implemented; `guitar_helper/lyrics/` is an empty package | Deferred behind playback correctness |
| Store interface | One `ISegmentStore` | Six role interfaces (Phase 4 O3/O4) | ISP: a god interface forced test doubles and the dispatcher to know about methods they never call |
| Persisted settings | — | `settings` key/value table (schema v9) | Nine migrations taught that a new knob should not cost a schema change |

---

## 1. Problem statement (unchanged from v0.1)

Playing electric guitar alongside a backing track presents a recurring friction
point: songs transition between tonally distinct sections (a clean intro into a
distorted body), but guitar rigs do not switch presets automatically. The
guitarist must either sit out sections where their tone does not fit, or reach
for the rig mid-song — both compromising the performance.

**Goal:** detect which section of a locally-stored song is playing, determine
its tonal character, and load the matching amp preset with no manual
intervention.

**Secondary goal:** a polished, distributable desktop application that other
guitarists can install and use.

---

## 2. Why every external dependency was removed (unchanged from v0.3)

The Spotify Audio Analysis endpoint was restricted for new apps in late 2024
(replaced by librosa in v0.2). The Spotify Web API runtime polling was removed
in v0.3 for three structural reasons: a 500 ms polling interval made lookahead a
fragile workaround; the API returns position but not audio samples; and
querying an internet service to locate your position in a local file is
architecturally incoherent.

`sounddevice` replaced it. The audio callback's frame cursor gives exact
position with sub-millisecond precision. **This decision has held** — it is the
foundation of the entire latency model in §7.

---

## 3. Three-tier architecture (as built)

```
TIER 3 — PRESENTATION                     guitar_helper/ui/
  MainWindow shell: mode sidebar + QStackedWidget + persistent transport
    Home mode     — playlist-first library, queue seeding
    Analysis mode — SegmentTimeline + segment table + edit toolbar
    Output mode   — preset table, dispatch log, calibration knob
  Qt-free cores:  EditorState, QueueState, editor/{validation,merge,preset_validation}
  Qt bridges:     EditorStateBridge, QueueStateBridge, qt_adapters
                          │ signals only — modes never touch Application directly
┌─────────────────────────▼───────────────────────────────┐
COMPOSITION ROOT                          guitar_helper/application.py
  Singletons:  store, MIDI port, dispatch log, dispatch offset
  Per track:   AudioBuffer, PositionTracker, PlaybackEngine, SegmentLookup,
               MidiDispatcher — rebuilt by attach(), torn down first
                          │
TIER 2 — PLAYBACK + MIDI                  guitar_helper/playback/, midi/
  AudioBuffer → PlaybackEngine (sounddevice callback) → PositionTracker
              → MidiDispatcher (own thread) → IMidiPort → loopMIDI → host → amp
                          │ reads segments + presets
TIER 1 — ANALYSIS                         guitar_helper/analysis/, db/
  AudioLoader → ISourceSeparator → FeatureExtractor → Segmenter
              → BaseToneClassifier → ISegmentStore (SQLite)
```

**Tier 1** runs offline, once per song. **Tier 2** runs during a session.
**Tier 3** is optional — `run_playback.py` and `run_analysis.py` exercise
Tiers 1 and 2 with no Qt import at all. That claim from v0.3 survived contact
with implementation.

---

## 4. Tier 1 — Analysis pipeline

### 4.1 Data flow

```
Audio file
  → AudioLoader.load()        — SHA-256 hash + duration, WITHOUT decoding
  → ISourceSeparator          — isolate the guitar stem (cached by hash)
  → AudioLoader.load_mono()   — 22.05 kHz mono float32 of the STEM
  → FeatureExtractor          — two 24×N matrices (see 4.3)
  → Segmenter                 — boundary timestamps in ms
  → BaseToneClassifier        — per-segment (tone_label, confidence)
  → ISegmentStore             — persist, keyed by file_hash
```

### 4.2 AudioLoader

Three distinct entry points, deliberately separated by cost:

| Method | Returns | Cost | Used by |
|---|---|---|---|
| `load(path)` | `(duration_ms, file_hash)` | Hash only — `soundfile.info()` for native formats, no decode | Analysis, batch discovery |
| `load_mono(path, sr=22050)` | `(y, sr)` mono float32 | Full decode + resample | Feature extraction |
| `decode(path)` | `(data, sr)` float32 `(frames, channels)` | Full decode at native rate, stereo preserved | Playback |

SHA-256 of the file bytes is the primary key throughout. Content-addressable,
so renaming a file never breaks the database; `analysis/file_locator.py`
re-finds a moved file by re-hashing candidates under the library root.

Native formats (WAV/FLAC/OGG/AIFF) go through `soundfile`; MP3/M4A/AAC through
`pydub`, which requires ffmpeg (now installed — v8.0.1; the primary target
format is iTunes `.m4a`).

### 4.3 FeatureExtractor — 24 features, two normalisations

| Rows | Feature | Role |
|---|---|---|
| 0 | Spectral flatness | Primary clean/distorted discriminator |
| 1 | Zero-crossing rate | Secondary; rises with distortion |
| 2 | RMS energy | Loudness; also drives the silence gate |
| 3 | Spectral centroid | Frequency centre of mass |
| 4–10 | Spectral contrast (7 bands) | Peak/valley ratio; high = clear harmonics |
| 11–23 | MFCC (13) | Timbre fingerprint |

The critical correction since v0.3 (ISSUE-002) is that **one normalisation
cannot serve both consumers**:

- `extract()` — per-song min-max. Preserves *relative* intra-song variation,
  which is what boundary detection needs.
- `extract_for_classification()` — fixed physical ranges
  (flatness/zcr/rms→[0, 0.25], centroid→[0, sr/2], contrast→[0, 40 dB],
  mfcc[0]→[−300, 50], mfcc[1:]→[−60, 60]). Preserves *absolute* spectral
  character, which is what comparing against a cross-song archetype needs.

Per-song normalisation applied to classification made every song's loudest
section look like metal. The archetypes in `archetypes.json` are only
meaningful in classification space.

> **Known inefficiency (minor).** `AnalysisPipeline.run()` calls both methods,
> and each independently recomputes the full librosa stack via `_stack_raw()`.
> Measured on a 189 s track: one `_stack_raw` costs 0.77 s against a 10.7 s
> analysis excluding separation — a ~7% saving, and negligible once separation
> (minutes) dominates the total. Worth deduplicating for cleanliness; it is not
> a performance fix.

### 4.4 Segmenter

`librosa.segment.agglomerative(feature_matrix, k)` over the per-song-normalised
matrix. `k` is forced by `--k` or estimated automatically.

**Auto-`k` (ISSUE-001).** The original checkerboard-kernel novelty curve had a
fixed kernel size, so its sensitivity scaled with song length — long songs were
systematically under-segmented. It was replaced by frame-to-frame cosine
distance, smoothed over ~1 s, with peaks required to exceed `mean + std` in
absolute height. An earlier attempt used `prominence=std`, which was inverted:
structured (high-std) songs got too few peaks and uniform songs too many.
`k = peaks + 1`, capped at 16.

**Minimum segment length.** Interior boundaries producing segments shorter than
1500 ms are dropped (greedy left-to-right; a too-short tail extends the previous
segment instead). Sub-second preset flapping is worse than a missed boundary.

Boundaries always include 0 and the authoritative `duration_ms` from
`AudioLoader.load()` — deriving the tail from frame counts introduced a
rounding mismatch.

### 4.5 Source separation

`ISourceSeparator` with two implementations: `NullSeparator` (passthrough, used
by tests and `--no-separate`) and `AudioSeparator` (htdemucs_6s via
python-audio-separator). Stems are cached at `stems/<file_hash>_guitar.wav`, so
re-analysis skips the expensive pass.

This is the fix for ISSUE-001/003. On a full mix, distorted guitar and loud
drums co-occur and the features compound in the *same* direction — v0.3's risk
table predicted this would help, and it did not; it made `edge` and `crunch`
inseparable. Segmenting and classifying the isolated stem resolved the overlap.

**Consequence:** archetypes must be calibrated on stems too. Running
`run_calibrate` on stems and then analysing with `--no-separate` compares
vectors from two different feature distributions.

### 4.6 ThresholdClassifier

Nearest-archetype by Euclidean distance in classification space.

```
confidence = 1 − (distance_to_best / distance_to_worst)
```

1.0 means an unambiguous winner, 0.0 that all archetypes are equidistant. This
replaced dividing by a theoretical maximum distance, which inflated every score.

Two escape hatches, both routing to `other`:

- **Silence gate** — normalised RMS below 0.02 returns `("other", 0.0)`
  immediately. A near-silent stem reads as low-energy regardless of tone and
  was misclassifying as `clean` (EXP-001: 12 of 14 near-silent segments).
- **Confidence floor** — below 0.2, return `other`. Holding the current preset
  beats dispatching a coin-flip.

`archetypes.json` (per-tone mean of manually-labelled segment vectors) is merged
*onto* `DEFAULT_ARCHETYPES`, so a tone absent from the calibration file keeps
its physics-motivated default rather than disappearing.

### 4.7 Calibration

`run_calibrate.py` reads every `manually_corrected = 1` segment from tracks not
excluded from calibration, re-extracts its classification vector from the cached
stem, and writes the per-tone mean.

**Statistic = mean.** EXP-001 initially favoured median to suppress
silent-segment outliers; once those were correctly relabelled `other`, clean-data
LOOCV preferred mean (macro-F1 0.693 vs 0.660). Revisit only if outlier
contamination returns.

**Current state:** 9 tracks, 123 segments, all human-verified. Macro-F1 ≈ 0.69
(from 0.434 pre-calibration); edge/crunch confusion eliminated (0.000). Per-tone
F1: metal .89, crunch .82, overdrive .71, other .69, clean .56, edge .48.
`overdrive` rests on only 5 labelled segments and is the least robust archetype.

### 4.8 Correction is ground truth, and is protected

Manual corrections set `manually_corrected = 1` and `confidence = 1.0`. They are
the *only* input to calibration, and they represent hours of listening.

In June 2026 a re-analysis reset all 129 of them to 0. They were unrecoverable —
OneDrive version history held only post-wipe copies, and `*.db` is gitignored.
Only `archetypes.json` survived, because it is in git.

The structural fix: `AnalysisPipeline.run()` raises `ManualCorrectionsExistError`
if any segment for the track is corrected, unless `discard_corrections=True`.
`run_batch` reports such tracks as SKIP. This replaced the previous
"re-analysis always replaces all segments" behaviour.

> **Open defect.** That guard covers the *intentional* overwrite. It does not
> cover a `save_segments` call that passes the guard and then fails partway —
> the delete and the partial insert are not wrapped in a transaction. See
> `mvp-readiness-review.md` §B1. This must be fixed before release.

---

## 5. Tier 2 — Playback and MIDI

### 5.1 Thread model

| Thread | Owns | Must never |
|---|---|---|
| PortAudio callback | frame cursor, tracker mirror, `last_tracker_lead_ms` | touch DB, MIDI, UI, or allocate |
| `MidiDispatcher` | `_last_tone`, `_last_pc` | touch SQLite |
| `LoadWorker` (QThread) | decode of one file | touch DB or playback objects |
| Qt main | the single sqlite3 connection, the runtime graph, all widgets | — |

The "callback is non-blocking" rule from v0.3 survived, but only after ISSUE-005
proved it is not self-enforcing: the callback itself was clean while a *different*
thread's repaint load starved it of the GIL. On a GIL-bound runtime, "keep the
callback cheap" is necessary but not sufficient — total main-thread work is part
of the audio contract.

### 5.2 PlaybackEngine

`sd.OutputStream` over `AudioBuffer.data`. The callback copies a slice, advances
the cursor under a lock, mirrors into `PositionTracker`, writes one float of
latency instrumentation, and raises `CallbackStop` at the end.

Default (low) latency and a small blocksize are deliberate. Larger buffers were
tried as an ISSUE-005 remedy and reverted: buffering does not cure GIL
starvation, it only fires preset changes earlier.

`viz_queue` is `None` in production. With no consumer the bounded queue filled
and every callback paid a `queue.Full` raise for nothing. It is re-enabled when
a spectrum view exists to drain it.

### 5.3 SegmentLookup

Loads the track's segments once, at construction, on the calling thread, and
resolves by `bisect` in memory. Not the per-query SQL of v0.3 — the dispatcher
thread cannot share the main thread's sqlite3 connection, and a query per tick
does not belong in a timing loop.

**Accepted consequence:** segment edits do not reach a playing track until it is
reloaded. Fixing this (reload-on-save, or a manual refresh) is the highest-value
item in the QOL backlog.

### 5.4 MidiDispatcher

Polls `PositionTracker` every 50 ms, adds the dispatch offset as lookahead,
resolves the tone, and sends a Program Change **only when the tone changes**.

Four outcomes, all recorded in the dispatch log:

| Outcome | Meaning |
|---|---|
| `send` | PC written to the port |
| `hold` | `other` tone, or the same PC already active — keep current preset |
| `unmapped` | Tone has no `presets` row — a data problem worth seeing |
| `gap` | Position fell between segments |

Logging every *decision* rather than every *send* is what makes a silent amp
diagnosable: it distinguishes "the dispatcher never fired" from "the dispatcher
fired and the chain swallowed it" — the exact ambiguity that made ISSUE-004 take
as long as it did.

`set_pc_map()` takes a plain dict, never the store, so the threading invariant
is enforced by the signature rather than by discipline. The logged position is
the *boundary*, not the cursor, so log lines line up with the segment table
instead of reading off by the lookahead.

### 5.5 MIDI transport and the VST3 finding

`MidoPort` opens a named loopMIDI port and **fails at construction** if it is
absent, so a missing virtual cable is reported before playback rather than
swallowed mid-song.

**ISSUE-004 (resolved).** Program Changes were confirmed correct end-to-end in
the host's MIDI monitor, yet the Neural DSP plugin did not switch. Root cause:
the **VST3 format does not deliver raw MIDI Program Change to hosted plugins** —
Steinberg's architecture requires host-side parameter mapping through
`IMidiMapping`. This is not a defect in the dispatch code.

**Resolution:** use the **VST2 build or the standalone app**. Both accept raw PC
and switch correctly (confirmed live). VST3 remains fine for audio processing
and manual preset selection.

This is a deployment constraint, not a bug, and it must appear in the setup
guide — no amount of correct dispatch code can work around it.

---

## 6. Tier 3 — Presentation

Three modes behind a sidebar, plus a persistent transport bar.

**Home** — playlist-first. A virtual "Library" entry (all songs, not a DB row)
plus real playlists. Double-click plays and seeds the queue from the shown
playlist. Playlist CRUD goes straight to the store on the main thread.

**Analysis** — `SegmentTimeline` (tone-coloured rects, click-to-seek, draggable
boundaries, hover tooltip) over a segment table with relabel / confirm /
confirm-all / merge / exclude / save / discard. Edits validate against an
in-memory working list and mutate nothing in the database until Save.

**Output** — diagnostic-first, the panel you open when the amp does not switch:
an editable preset table with a **per-row test-send** (proves the
loopMIDI→host→plugin chain with nothing playing), the live dispatch log, the
active-preset indicator, and the dispatch-offset knob with one-step Revert.

### 6.1 The Qt-free core

`EditorState` and `QueueState` hold all editor and queue logic and **import no
Qt at all**. `EditorStateBridge` / `QueueStateBridge` translate their plain
callback events into Qt signals. Likewise `editor/validation.py`,
`editor/merge.py` and `editor/preset_validation.py` are pure functions over
dataclasses.

The payoff is direct: the logic that decides whether an edit is legal is
unit-tested without a `QApplication`, and the same validation module backs both
the GUI editor and the CLI correction tool. Three of the four largest test files
target these modules.

### 6.2 SegmentTimeline (ISSUE-005)

The user's observation was the discriminating test: playback was choppy **only
while Analysis mode was visible**. Phase 4 O1 had already added a skip for
playhead repaints when Analysis is hidden, so visibility was the variable —
pyqtgraph's 20 Hz scene repaint was starving the audio callback of the GIL.

The waveform was removed entirely (also a UX decision — it earned little).
`SegmentTimeline` is a plain `QWidget` whose `paintEvent` draws a handful of
rects and a line, and which repaints only when the playhead crosses a **pixel
column** rather than on every timer tick. `pyqtgraph` is now unused.

---

## 7. Latency model

**Goal:** the Program Change lands on the *audible* tone boundary.

```
optimal_lookahead = L_chain + poll_wait − L_out
```

- `L_out` — how far the position cursor leads what you hear (device output
  latency + one block), measured live from PortAudio's DAC clock.
- `poll_wait` — average wait after a boundary crossing (~half the 50 ms poll
  interval).
- `L_chain` — loopMIDI → host → plugin *audible* switch time. **Not measurable
  from Python**; supplied by ear, with an audio-loopback wizard as later
  automation.

Because boundaries are seconds apart, this collapses to **one global knob** — no
per-segment correction is needed. That knob is `dispatch_offset_ms`, persisted
in the `settings` table, applied live to the running dispatcher, and reversible
in one step.

Note that `L_out` and the old hardcoded 75 ms lookahead **stack**, so Program
Changes were most likely firing *early*, not late; the correction direction is
probably less lead. "On time by ear" is consistent with slightly early.

`measure_latency.py` reports the measurable parts (tracker lead, poll jitter,
send cost) and prints a data-driven starting value. **Status: built, not yet run
on the rig.** The `settings` table is empty, so the offset is still at its
default.

---

## 8. SOLID — as applied

v0.3's SOLID section described interfaces that were never built. This one
describes the seams that exist and earn their keep.

### 8.1 Single Responsibility

`AnalysisPipeline` is still the only module that knows the pipeline order, and
each step remains ignorant of the others. Modules are small and purpose-shaped:
`dispatch_log.py` is 61 lines, `merge.py` is 40.

The one strained class is `MainWindow` (441 lines), which owns the mode shell,
the async load pipeline, queue synchronisation, playlist-analysis context, two
timers, loop checking and track-finished detection. A `SessionCoordinator`
extraction is the obvious remedy if the QOL pass makes it heavier.

### 8.2 Open/Closed

Five real extension seams: `BaseToneClassifier`, `ISourceSeparator`,
`IMidiPort`, the store role ABCs, and the `settings` key/value table.

The settings table is the best of them and the most hard-won: after nine
schema migrations, adding a knob was made to cost a *key* rather than a column.

`IRenderer` from v0.3 was never built, and correctly so — the renderers it
would have abstracted (`SpectrumAnalyzer`, `WaveformView`, `SegmentOverlay`)
were removed before a second implementation ever existed. An abstraction with
one implementation is a liability.

### 8.3 Liskov

`NullSeparator` / `AudioSeparator` and `MockMidiPort` / `MidoPort` are
behaviourally substitutable, and the test suite *depends* on that being true
rather than merely asserting it — the entire suite runs against `MockMidiPort`
and touches no hardware.

### 8.4 Interface Segregation — the strongest result of Phase 4

`ISegmentStore` began as a single 11-method interface. Phase 4 O3 split it by
role:

| Interface | Methods | Consumer |
|---|---|---|
| `ITrackCatalog` | 4 | Home mode, library listing |
| `ISegmentReader` | 2 | `SegmentLookup` |
| `ISegmentEditor` | 5 | `EditorState`, correction CLI |
| `IPresetStore` | 2 | **`MidiDispatcher`** |
| `ISettingsStore` | 3 | composition root |
| `IPlaylistStore` | 6 | Home mode |

`MidiDispatcher` — the most timing-sensitive component — now depends on a
two-method interface instead of an eleven-method one.

The confirming evidence came in O4: folding `get_setting`/`set_setting` into
`ISegmentStore` broke `test_pipeline`'s `MockStore`, which had no reason to know
about settings. That breakage *is* the ISP violation. The fix was
`IAppStore = ISegmentStore + ISettingsStore`, composed only where the composition
root needs both.

The combined `ISegmentStore` survives as a deprecated alias for consumers that
genuinely span roles.

### 8.5 Dependency Inversion

`Application` is a real composition root: it constructs the concretes and
injects them through interfaces, which is why the whole suite runs with an
in-memory store and a mock port.

```
MidiDispatcher   → IPresetStore, IMidiPort    (not sqlite3, not mido)
SegmentLookup    → ISegmentReader
AnalysisPipeline → ISegmentStore, BaseToneClassifier, ISourceSeparator
EditorState      → ISegmentStore
```

**Known violation:** `correction/cli.py` (Tier 1) imports
`guitar_helper.ui.editor.validation` (Tier 3). The module is Qt-free, so nothing
breaks at runtime, but the dependency points from a lower tier to a higher one —
contradicting the tier rule in §3. Moving `ui/editor/` to a neutral
`guitar_helper/editing/` package would resolve it.

---

## 9. Data model — schema v9

```sql
CREATE TABLE tracks (
    file_hash            TEXT PRIMARY KEY,   -- SHA-256 of file bytes
    filename             TEXT NOT NULL,
    title                TEXT,
    artist               TEXT,
    duration_ms          INTEGER NOT NULL,
    analysed_at          TEXT NOT NULL,      -- ISO 8601 UTC
    calibration_excluded INTEGER NOT NULL DEFAULT 0,
    source_path          TEXT
);

CREATE TABLE segments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash          TEXT NOT NULL REFERENCES tracks(file_hash),
    start_ms           INTEGER NOT NULL,
    end_ms             INTEGER NOT NULL,
    tone_label         TEXT NOT NULL REFERENCES presets(tone_label),
    confidence         REAL NOT NULL,
    manually_corrected INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE segments_calibration (...);  -- lazy pre-edit snapshot, same shape
CREATE TABLE presets   (tone_label TEXT PRIMARY KEY, preset_name TEXT, pc_number INTEGER);
CREATE TABLE playlists (id INTEGER PRIMARY KEY, name TEXT UNIQUE, created_at TEXT);
CREATE TABLE playlist_tracks (playlist_id … ON DELETE CASCADE, file_hash …, position INTEGER,
                              PRIMARY KEY (playlist_id, file_hash));
CREATE TABLE settings  (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
```

Corrections against v0.3: there is **no `segments.preset_name` column** (preset
names live in `presets`, one row per tone); `tone_label` carries a foreign key
to `presets`; and four tables were added across versions 7–9.

**`segments_calibration`** holds a lazy, idempotent snapshot taken before the
first edit of a track. Merge is physical — it deletes absorbed rows — so
calibration would otherwise lose the original granularity it was fitted on.

**MIDI preset mapping (fresh install):**

| Tone | PC |
|---|---|
| clean | 0 |
| edge | 1 |
| overdrive | 2 |
| crunch | 3 |
| metal | 4 |
| other | −1 (no dispatch) |

A deliberate clean→metal gain progression. `ambient` is deferred pending
calibration data.

> **Known defect (blocking release).** `_seed()` runs only when
> `schema_version` is absent, and three of the five historical changes to
> `_DEFAULT_PRESETS` shipped without a migration. A database created before the
> gain-ramp reorder retains `clean 0, crunch 1, metal 2, edge 3, overdrive 4`;
> one created before Phase 2 has no `edge` row at all, making `edge` segments
> unstorable. `presets.pc_number` also has no UNIQUE constraint. Full analysis:
> `mvp-readiness-review.md` §B2.

---

## 10. Technology stack

| Library | Tier | Role |
|---|---|---|
| `librosa` | 1 | Feature extraction, agglomerative segmentation |
| `soundfile` | 1 + 2 | Native-format IO and metadata |
| `pydub` | 1 | MP3/M4A/AAC decoding (needs ffmpeg) |
| `numpy` | 1 + 2 | Array operations |
| `scipy` | 1 | Peak finding and smoothing for auto-`k` |
| `mutagen` | 1 | Title/artist tags in batch analysis |
| `sqlite3` | 1 + 2 | Persistence (stdlib) |
| `sounddevice` | 2 | Audio output stream + callback |
| `mido` + `python-rtmidi` | 2 | MIDI Program Change to loopMIDI |
| `PySide6` | 3 | UI framework (LGPL, distributable) |
| `audio-separator` | 1 (optional) | htdemucs_6s stem isolation — separate heavy install |

`pyqtgraph` and `requests` remain declared but are **unused** (waveform removed;
lyrics unimplemented). Both should be dropped before packaging.

### System dependencies

| Dependency | Notes |
|---|---|
| ffmpeg | MP3/M4A/AAC decode. Installed (v8.0.1) |
| loopMIDI | Virtual MIDI cable; must be running before the app starts |
| **Neural DSP VST2 or standalone** | **VST3 cannot receive raw Program Change — see §5.5** |
| PortAudio | Bundled in the sounddevice wheel |

`python-rtmidi` has no cp314 wheel and builds from source via meson (needs
MSVC), so it is runtime-only and excluded from CI, which uses `MockMidiPort`.

---

## 11. Risk table

| Risk | Status | Mitigation |
|---|---|---|
| Spotify API dependencies | **Resolved** | Removed entirely (v0.2, v0.3) |
| Full-mix features dominated by drums/bass | **Resolved** | Stem separation (§4.5) |
| Per-song normalisation destroys classification | **Resolved** | Two normalisations (§4.3, ISSUE-002) |
| Auto-`k` scaling with song length | **Resolved** | Cosine-distance novelty (§4.4, ISSUE-001) |
| UI repaint starving the audio callback | **Resolved** | SegmentTimeline (§6.2, ISSUE-005) |
| VST3 not receiving Program Change | **Resolved** | Use VST2/standalone (§5.5, ISSUE-004) |
| Loss of manual corrections | **Partially mitigated** | Re-analysis guard in place; **non-atomic `save_segments` is still open** |
| Migrated DBs with a divergent preset map | **OPEN — blocking** | v10 reconcile migration not yet written |
| `overdrive` archetype weak (5 labels) | Open | Label more segments and recalibrate |
| Segment edits need a track reload | Open (accepted) | Reload-on-save is the top QOL item |
| Dispatch offset never calibrated on the rig | Open | Run `measure_latency`, enter the value in Output |
| No packaging artifacts | **OPEN — blocking** | pyproject, PyInstaller spec, per-user data dir |
| loopMIDI absent at launch | Open | Currently fatal; should degrade to a null port |

---

## 12. Open questions

| # | Question | Status since v0.3 |
|---|---|---|
| 1 | Correction tool: CLI or GUI? | **Answered — both.** GUI editor shipped in Phase 4 O3; CLI retained |
| 2 | Tone taxonomy and PC mapping | **Answered** — six tones, clean→metal ramp (§9) |
| 3 | asyncio vs threading for the MIDI watcher | **Answered** — a plain daemon thread; no async anywhere |
| 4 | Archetype calibration against real songs | **Answered** — `run_calibrate`, 9 tracks, macro-F1 ≈ 0.69 |
| 5 | Lyrics: local `.lrc` or LrcLib fetch? | Still open — package is empty; decision unchanged (auto-fetch with local fallback) |
| 6 | Distribution: PyInstaller `.exe` | **Still open and now the critical path.** App data dir decided (`%LOCALAPPDATA%\GuitarHelper`); nothing else built |
| 7 | Boundary editing in the correction tool | **Answered** — drag on the timeline plus CLI `<idx> <start> <end> <label>`; typed min:sec entry is a QOL item |
| 8 | *(new)* How should a preset reconcile treat a user-edited PC? | Open — O4 made presets editable, and the schema cannot distinguish drift from intent (§9) |
| 9 | *(new)* Should the app run with MIDI disabled? | Open — currently fatal at startup, which blocks analysis-only use |

---

*Guitar Performance Assistant — Project Design Report v0.4*
*2026-08-04 — as-built at commit `de249c7`*
