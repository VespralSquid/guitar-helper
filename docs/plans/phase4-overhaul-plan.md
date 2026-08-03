# Phase 4 UI Overhaul Plan — Home / Media / Analysis / Output

_Status: APPROVED 2026-07-13. Supersedes the milestone roadmap of `phase4-ui-plan.md`
(M1-M3 shipped as designed there; M4-M7 are re-homed below). Source spec:
`docs/new feature specs/phase4_ui_overhaul_specs`._

## 1. Rationale

### 1.1 Correcting the spec's diagnosis

The overhaul spec attributes load lag and playback persistence to "lack of separation
between playback and librosa analysis." Analysis already runs offline (Phase 2 batch
pipeline); librosa at runtime only **decodes** audio. The three errors in
`docs/debug/List of known errors` traced to two lifecycle/threading gaps
(`docs/Report/phase4-ui-architecture.md` §6), both fixed in **M0** (2026-07-13):

- **Gap A — no teardown on reload.** `Application.load()` rebuilt the runtime graph
  without stopping the old engine's `sd.OutputStream` or joining the old dispatcher
  thread → orphaned uncontrollable playback (Error 3) and two streams contending for
  the device (Error 2). Fixed: `attach()` validates first, then calls `stop()` before
  wiring the new graph.
- **Gap B — synchronous load.** Hash + full librosa decode ran inline on the Qt main
  thread → multi-second GUI freeze (Error 1). Fixed: `Application.decode()` (no DB, no
  Qt, no playback state) runs on a `LoadWorker(QThread)`; `Application.attach()` runs
  on the main thread when the worker's `decoded` signal arrives.

The four-area restructure below is justified by **scale and separation of concerns**
(the single window cannot absorb playlists, queueing, correction, and MIDI config),
not by the bugs.

### 1.2 SOLID assessment of the M1-M3 code (drives the design rules below)

- **SRP** good at module level (theme.py, Qt-free `EditorState`, thin views); at risk
  in `MainWindow`, the sole wiring point → **rule: one wiring module per mode**.
- **OCP/LSP/DIP** good: ABCs + composition root + injectable store/port. Keep.
- **ISP** weakest: `ISegmentStore` is a fat 11-method interface spanning analysis
  writes, playback reads, correction edits, library listing, presets, calibration
  flags → **rule: split into role interfaces** (§3).

### 1.3 Constraints (unchanged, non-negotiable)

- One SQLite connection, main-thread only. Worker threads may hash/decode files but
  never touch the store.
- Audio callback strictly non-blocking; dispatcher on its own thread; `PositionTracker`
  the only cross-thread state.
- `presets` table = single source of truth for tone→PC; no hardcoded PCs; `other`
  holds, never dispatches.
- Additive schema migrations only. **v7 = `segments_calibration`** (already approved in
  `phase4-ui-plan.md` §3); **v8 = playlists**.
- `correction/cli.py::_parse_command` symbol + contract preserved (tests import it).
- Correction-protection guard (`ManualCorrectionsExistError`) untouched.
- Qt-free state + bridge (`EditorState`/`EditorStateBridge`) is the established idiom —
  every new stateful feature follows it.

## 2. Target architecture

One `QMainWindow` shell hosting **modes** in a `QStackedWidget`, switched by a left
sidebar; the transport strip stays persistent below the stack, and a **shell-level
right sidebar (visible in every mode) shows now-playing info and the manipulable
queue**. Playback continues across mode switches — the runtime graph lives in
`Application`, not in any mode.

_Revision 2026-07-13 (acceptance criteria, §6):_ the standalone **Media mode is
absorbed** — Home is playlist-first and the queue lives in the shell sidebar, so the
app has three modes: Home, Analysis, Output.

| Mode | Spec components | Content |
|---|---|---|
| **Home** | 1, 2, 3, 4 | Playlist-first: playlist list (virtual "Library" playlist of all songs when none exist + create-first-playlist prompt); opening a playlist shows its songs (Title, Artist, Date added, progress); create playlist, move songs in/out; double-click plays; Analyze button per playlist |
| **Analysis** | 5, 6, 7 | Playlist-scoped editor: header names the playlist, prev/next traverses its songs; song list + segment table + **segment timeline** (waveform removed 2026-07-13); M4/M5 edit operations |
| **Output** | 8 | MIDI config: presets table view/edit (PC-only), live dispatch log + active-preset indicator |

Wiring rule (SRP): `main_window.py` stays a shell — constructs modes, owns shared
timers and the transport. Each mode gets its own module owning its widgets and signal
wiring (e.g. `ui/modes/home.py`, `media.py`, `analysis.py`, `output.py`).

## 3. Interface split (ISP refactor, pure — no behavior change)

Decompose `ISegmentStore` in `db/interfaces.py` into role ABCs:

- `ITrackCatalog` — `list_tracks`, `save_track`, `set/get_calibration_excluded`
- `ISegmentReader` — `get_segment`, `get_segments`
- `ISegmentEditor` — `save_segments`, `update_segment`, `delete_segment`,
  `ensure_calibration_copy`, `get_calibration_segments`
- `IPresetStore` — `get_presets`, `save_preset`
- `IPlaylistStore` (new, O2) — playlist CRUD + membership

`SQLiteSegmentStore` implements all of them (one class, one connection). Consumers
type-hint only the role they need (`MidiDispatcher` → `IPresetStore`, `SegmentLookup` →
`ISegmentReader`, `LibraryPanel` → `ITrackCatalog`, …). `ISegmentStore` may remain as a
deprecated alias inheriting all roles during the transition.

## 4. Milestone roadmap

### M0 — Foundation fixes ✅ (done 2026-07-13)
`Application.decode()/attach()` split, teardown-before-rebuild, `play/pause/seek` None
guards, `ui/load_worker.py`, async wiring + loading state in `MainWindow`,
`loadFinished` signal. Tests: lifecycle unit tests + async smoke tests (158 total).

### O1 — Shell & navigation ✅ (done 2026-07-13)
- `QStackedWidget` + mode sidebar; persistent transport strip; per-mode wiring modules.
- Home mode: library overview fed by existing `list_tracks()`.
- Current editor layout moves into the Analysis mode unchanged.
- _Demo:_ switch modes while a track plays; playback and transport uninterrupted.

### O2 — Playlist-first Home + queue sidebar (NEXT; merged former O1.5 + O2 per AC §6)
- Schema **v8** (additive): `playlists(id, name, created_at)`,
  `playlist_tracks(playlist_id, file_hash, position)`; `IPlaylistStore` methods on
  `SQLiteSegmentStore`. `Track`/`list_tracks()` gain `analysed_at` ("Date added" —
  column already exists, SELECT change only).
- Home rework (AC1/2/4/5): playlist list; **virtual "Library" playlist** (all songs,
  not a DB row) shown when no playlists exist, with create-first-playlist prompt;
  song table (Title, Artist, Date added, progress); create-playlist button; move
  songs in/out (add-to-playlist / remove actions).
- **Double-click plays** (single click selects): async load + auto-play on attach +
  queue seeded with the rest of the playlist; app **stays in Home** (auto-switch to
  Analysis on load is removed — Analysis is entered via playlist Analyze buttons, O3).
- Qt-free `QueueState` (`ui/state/queue_state.py`: ordered hashes, current index,
  shuffle with stable un-shuffle, repeat modes) + `QueueStateBridge`. Queue is
  **in-memory session state**.
- Shell **right sidebar** (AC3): now-playing (title — artist, text only) + queue view
  with manipulation (reorder, remove, clear, play-next). Transport gains next/prev;
  track-finished detection on `_pos_timer` advances the queue.
- _Demo:_ create a playlist, move songs in, double-click to play with now-playing and
  queue shown, skip/prev, reorder queue; playlist survives restart, queue doesn't.
- **Re-assess with user before starting O3.**

### O3 — Analysis (absorbs old M4 + M5; reshaped by AC §6)
Editor internals exactly per approved `phase4-ui-plan.md` §§2-4:
- `ui/editor/validation.py` extracted from `_parse_command` (symbol preserved);
  relabel dropdown, draggable boundary `InfiniteLine`s with live validation,
  confirm/confirm-all, exclude toggle, save/discard + dirty indicator;
  `store.delete_segment`.
- `ui/editor/merge.py` (same-tone-run merge); schema **v7** `segments_calibration` +
  idempotent `ensure_calibration_copy` (lazy copy on first edit).
New shape (AC7/8, revised 2026-07-13 — waveform REMOVED):
- Entered via a playlist's **Analyze** button; **header names the playlist**;
  **prev/next page-style traversal** through the playlist's songs.
- The pyqtgraph waveform + overlay are **gone** (user judged the waveform unnecessary;
  its 20 Hz scene repaint was also the confirmed ISSUE-005 cause). `SegmentTimeline`
  (`ui/views/segment_timeline.py`, custom `paintEvent`, pixel-gated playhead repaints)
  is the seek/selection surface. **Boundary dragging lands on the timeline** (drag a
  band edge; live `validation.validate_boundary` feedback), superseding the old M4
  draggable-`InfiniteLine` design.
- Layout prioritizes the playlist song list + segment table above the timeline strip.
- _Demo:_ full CLI-parity editing in the GUI while listening; traverse a playlist
  song-by-song; merge preserves originals in `segments_calibration`; re-analysis
  guard still honors corrections.

### O4 — Output ✅ (done 2026-08-03; absorbs old M6)
- Preset panel: view/edit `presets` rows — PC numbers **and** preset names (no CC/channel
  schema change; writes via `save_preset`). Names were added to O4 scope so rows can be
  labelled with the actual amp patch. Validation is Qt-free in
  `ui/editor/preset_validation.py`: PC 0-127, no duplicate mapping, `other` locked at -1.
- Per-row **test-send** — fires the selected PC straight at the port with nothing playing.
  Not in the original scope; added because it is the cheapest possible ISSUE-004 probe.
- `DispatchLogBuffer` (`deque(maxlen=200)`, `playback/dispatch_log.py`), optional
  `log_sink` on `MidiDispatcher` (default `None`, existing tests untouched),
  `_dispatch_timer` (100 ms, running only while Output is visible) draining into a live
  log + active-preset indicator — the ISSUE-004 diagnostic view. Every decision is
  logged, holds and gaps included, not only sends.
- **Dispatch offset knob** (added to O4): schema **v9** `settings(key, value)` +
  `ISettingsStore`; the hardcoded `lookahead_ms=75` becomes a persisted, live-applied
  `dispatch_offset_ms` with one-step Revert. This is the correction half of
  `docs/Report/latency-calibration-analysis.md` — the QOL calibrate *wizard* stays
  deferred, this is the engine under it.
- ISP note: `ISettingsStore` is **not** folded into `ISegmentStore`; a new
  `IAppStore` composes the two for the composition root. Folding it broke
  `test_pipeline`'s `MockStore`, which is precisely the fat-interface failure §1.2
  identified.
- _Demo:_ watch every PC send/hold with timestamps while a track plays; remap a PC and
  hear the change without reloading the song.

### Deferred
Spectrum + lyrics (old M7), CC/channel mapping, queue persistence, `ambient` preset
re-add, pointing `run_calibrate` at `segments_calibration`; **cover art + richer song
metadata** read from embedded tags (user's files come from iTunes/Bandcamp — mutagen,
new dependency); **recalibrate-from-UI** (worker-thread calibration + per-tone summary
was designed, then dropped from O3 scope 2026-07-13 — calibration stays CLI).

## 5. Testing strategy

Per milestone, same shape as `phase4-ui-plan.md` §9:
- Qt-free unit tests for all logic: `QueueState` (shuffle determinism with seeded rng,
  repeat modes, exhaustion), `validation`, `merge`, repository additions (v7/v8
  migrations, playlist CRUD, `ensure_calibration_copy` idempotency).
- pytest-qt smoke per mode: builds headless, mode switching keeps timers/transport
  alive, async loads awaited via `loadFinished` (`tests/test_ui_smoke.py` pattern).
- Invariants re-checked each milestone: no DB off main thread, dispatcher/audio
  callback untouched (except opt-in `log_sink`), parameterized SQL only, additive
  migrations only.

## 6. Acceptance criteria (user, 2026-07-13) and disposition

- **AC1** — Home shows song name, artist, date added, with buttons to enter Analysis.
- **AC2** — Clicking a song plays it; UI reflects the selection via cover art or song
  name, like standard players (Spotify/iTunes).
- **AC3** — Right-side window sidebar shows the now-playing properties alongside the
  queue, with the ability to manipulate the queue.
- **AC4** — Home shows **playlists, not songs**. With no playlists: a default
  "Library" playlist containing all stored songs + text encouraging creating the
  first playlist.
- **AC5** — Button to create a playlist; ability to move songs in and out of playlists.
- **AC6** — In Analysis, playback is consistent and editing is available while
  listening in real time.
- **AC7** — In Analysis, segmentation and segment→tone mapping are editable. Analysis
  is entered via playlist-associated buttons; a header names the playlist; songs are
  traversable page-style (forward/back for next/previous song to analyze).
- **AC8** — Analysis prioritizes playlists/songs and segmentation over the waveform.

| AC | Disposition |
|---|---|
| AC1, AC2, AC3, AC4, AC5 | **O2 (merged milestone, NEXT)** — playlists schema v8, playlist-first Home, double-click plays, queue sidebar. "Date added" = existing `analysed_at`. |
| AC6 | Edit-while-listening = O3 by design. "Consistent" = **ISSUE-005 (REOPENED)** — not satisfiable by UI work; tracked in docs/debug/, user investigating. |
| AC7, AC8 | **O3** — edit ops per old plan + playlist-scoped entry, header, prev/next traversal, waveform demoted to compact strip. |

**Decisions (user, 2026-07-13):** double-click plays, single click selects; now-playing
is **text-only** for now — files come from legitimate sources (iTunes/Bandcamp) with
embedded metadata, so deferred cover-art/tag work reads those tags (mutagen);
**recalibrate-in-UI dropped** (stays CLI, design parked in Deferred); Media mode
absorbed into Home + shell queue sidebar (three modes remain); **one merged O2
milestone, then re-assess** before O3; **waveform removed entirely** in favour of a
segment-colored timeline (post-O2 re-assess — also the ISSUE-005 fix, since the
choppiness appeared only while the pyqtgraph waveform was visible).
