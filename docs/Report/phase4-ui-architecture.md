# Phase 4 UI — As-Built Architecture

_Written 2026-07-10. Describes the Phase 4 UI as implemented through milestone M3
(`guitar_helper/ui/`, `guitar_helper/playback/`, `guitar_helper/application.py`).
This is a reference for what the code actually does, not a design proposal — for
the original intent/rationale see `docs/phase4-ui-plan.md`. For fix proposals for
the bugs referenced in section 6, see `docs/debug/List of known errors`._

---

## 1. Layer overview

The app is built in four layers, strictly one-directional in their dependencies
(views depend on state, state never depends on views):

**Composition root — `guitar_helper/application.py::Application`**
Constructs every concrete object and wires them through their interfaces
(`ISegmentStore`, `IMidiPort`). It is the only place that knows how a track goes
from a file path to a playable, MIDI-dispatching runtime graph. `guitar_helper/ui/app.py::run()`
builds the `QApplication`, constructs one `Application`, hands it to `MainWindow`,
and calls `qapp.exec()`.

**Qt-free core — `guitar_helper/ui/state/editor_state.py::EditorState`**
Holds the loaded track's segments and the current selection. Deliberately imports
nothing from `PySide6` or `pyqtgraph` (enforced by a comment at the top of the
file) so it can be constructed and unit-tested without a `QApplication`
(`tests/test_editor_state.py`). State changes are announced through a plain
callback list (`subscribe(callback)` / `_emit(StateEvent)`), not Qt signals.

**Qt adapter — `guitar_helper/ui/state/state_bridge.py::EditorStateBridge`**
A thin `QObject` that subscribes to an `EditorState` instance and translates each
`StateEvent` into a typed Qt `Signal` (`loaded`, `selectionChanged`, `segmentsChanged`,
`dirtyChanged`, `savedChanged`). This is the seam between framework-agnostic logic
and Qt views — everything upstream of this class has no Qt dependency; everything
downstream talks Qt signals/slots.

**Views** (all thin, own no business logic):
- `ui/views/waveform_view.py::WaveformView` — pyqtgraph `PlotWidget`. Downsamples
  the full track to a fixed 3000-column min/max envelope once at load time
  (`theme.WAVEFORM_COLUMNS`), so render cost is constant regardless of track
  length. Owns the playhead (`pg.InfiniteLine`) and emits `seekRequested(ms)` on
  click.
- `ui/views/segment_overlay.py::SegmentOverlay` — draws one non-draggable
  `pg.LinearRegionItem` per segment, colored/brushed by tone via `theme.py`.
- `ui/models/qt_adapters.py::SegmentTableModel` — `QAbstractTableModel` over the
  segment list (Start/End/Tone/Confidence/Corrected columns).
- `ui/panels/library_panel.py::LibraryPanel` — `QListWidget` populated from
  `store.list_tracks()`; emits `trackChosen(Track)` on double-click. Tracks with
  no `source_path` on record are shown disabled (can't be reloaded).
- `ui/transport.py::TransportControls` — play/pause/stop buttons, loop checkbox,
  seek slider, position label. All playback buttons start disabled
  (`set_enabled_playback(False)`) until a track loads. The seek slider tracks a
  `_slider_pressed` flag so position-timer updates don't fight the user's drag.

**`ui/main_window.py::MainWindow`** is the wiring point: constructs all of the
above, connects their signals to `PlaybackController`/`EditorState` methods, and
owns the one `QTimer` that polls playback position (`_pos_timer`, see §3).

---

## 2. Object lifecycle

`Application` holds two categories of state:

| Object | Built | Lifetime |
|---|---|---|
| `store` (`ISegmentStore`) | `Application.__init__` | Singleton — one `sqlite3` connection for the app's life |
| `port` (`IMidiPort`) | `Application.__init__` | Singleton — one MIDI port for the app's life |
| `viz` (`VisualizationBridge`) | `Application.__init__` | Singleton — one bounded queue for the app's life |
| `buffer` (`AudioBuffer`) | `Application.load(path)` | Rebuilt every load |
| `tracker` (`PositionTracker`) | `Application.load(path)` | Rebuilt every load |
| `engine` (`PlaybackEngine`) | `Application.load(path)` | Rebuilt every load |
| `lookup` (`SegmentLookup`) | `Application.load(path)` | Rebuilt every load |
| `dispatcher` (`MidiDispatcher`) | `Application.load(path)` | Rebuilt every load |

`Application.load()` (`application.py:59-73`):

```python
def load(self, path: str | Path) -> str:
    file_hash = self._loader.hash_file(path)
    if not self.store.get_segments(file_hash):
        raise NoSegmentsError(...)
    self.buffer = AudioBuffer.from_file(path, self._loader)
    self.tracker = PositionTracker(self.buffer.sr)
    self.engine = PlaybackEngine(self.buffer, self.tracker, self.viz.queue)
    self.lookup = SegmentLookup(self.store, file_hash)
    self.dispatcher = MidiDispatcher(
        self.store, self.lookup, self.tracker, self.port, channel=self.channel
    )
    return file_hash
```

**Today's gap:** this reassigns all five per-track attributes to brand-new objects
without ever calling `self.stop()` on the objects being replaced. `Application.stop()`
(lines 86-90) exists and correctly tears both `engine` and `dispatcher` down — it is
simply never called from `load()`. See §6.

---

## 3. Threading / process model

The app is a single OS process. **Three threads run concurrently once a track is
playing; no `QThread`, `QRunnable`, or `multiprocessing` exists anywhere in the
codebase today.**

| Thread | Spawned by | Responsibility | Synchronization touched |
|---|---|---|---|
| **Main / Qt thread** | `QApplication.exec()` (`ui/app.py:30`) | Every widget, `EditorState`, all DB reads/writes via `store`, `Application.load/play/pause/stop/seek`, the `_pos_timer` tick | Owns the sole `sqlite3` connection — never touched from another thread |
| **PortAudio audio callback thread** | Native thread inside `sounddevice`, spawned when `PlaybackEngine.play()` calls `sd.OutputStream(...).start()` (`playback/playback_engine.py:40-49`) | Runs `PlaybackEngine._callback()` (`playback_engine.py:79-99`) — copies one chunk from `AudioBuffer.data`, advances the frame cursor, mirrors it into `PositionTracker`, pushes `(cursor, chunk)` onto the viz queue. **Strictly non-blocking**: no DB, MIDI, or UI calls, per the project's real-time-callback rule (CLAUDE.md) | `PositionTracker._lock` (write via `set_cursor`); `VisualizationBridge.queue.put_nowait` (bounded, drops silently on `queue.Full` — never blocks the callback) |
| **`MidiDispatcher` thread** | `threading.Thread(daemon=True)`, started by `Application.play()` → `dispatcher.start()` (`application.py:75-77`, `playback/midi_dispatcher.py:83-88`) | Loop in `_run()` (`midi_dispatcher.py:96-98`): every `poll_interval_s` (default **0.05s / 50ms**), calls `tick()` — reads `PositionTracker.position_ms` + 75ms lookahead, resolves the active tone via `SegmentLookup` (an in-memory `bisect` snapshot built once at `load()` time, never a per-tick DB query — the class docstring explicitly calls out that SQLite connections aren't thread-shareable), sends a Program Change via `IMidiPort` only when the resolved tone changes | `PositionTracker._lock` (read via `position_ms`); own `threading.Event` (`_stop`) + `Thread.join(timeout=1.0)` in `stop()` for clean shutdown |

Both background threads are lightweight and purpose-built:
- `PositionTracker` (`playback/position_tracker.py`) is a single `int` frame
  cursor guarded by one `threading.Lock`, converted to ms on read. This is the
  only piece of state the audio thread and the dispatcher thread both touch.
- `VisualizationBridge` (`playback/visualization_bridge.py`) is a bounded
  `queue.Queue(maxsize=64)`. Producer (audio thread) never blocks — `put_nowait`
  + swallow `queue.Full`. Nothing currently drains this queue from the UI in M1-M3
  (spectrum/waveform-scrub visualization is deferred to M6/M7 per `SAVE_STATE.md`),
  so today the queue simply fills and drops once playback starts.
- `MidiDispatcher.stop()` is the one place in the app that does a bounded blocking
  wait (`Thread.join(timeout=1.0)`) — always called from the main thread via
  `Application.stop()`.

**No thread or process backs `Application.load()`.** Everything in §2's `load()`
body — `hash_file` (full-file SHA-256), the DB segment check, and
`AudioBuffer.from_file()` (`playback/audio_buffer.py:33-37` → `AudioLoader.decode()`,
`analysis/audio_loader.py:53-66`, a full eager `librosa.load(path, sr=None,
mono=False)` decode of the whole track into memory) — runs inline, synchronously,
on the Qt main thread, whichever call site invokes it (currently
`MainWindow._load_path`). This blocks the GUI event loop for the entire duration.
See §6.

---

## 4. `AudioBuffer.data` — why it's safe unlocked

`AudioBuffer.data` (`playback/audio_buffer.py`) is a plain numpy array with no
lock guarding it. This is safe today because of a strict write-once/read-many
pattern: it is written exactly once, by `AudioLoader.decode()` inside
`AudioBuffer.from_file()`, on whichever thread calls `Application.load()`
(currently the main thread). After construction, it is only ever *read*:
- Continuously, by the audio callback thread (`PlaybackEngine._callback`'s
  `chunk = self._buffer.data[start:end]`).
- Once, by `WaveformView.load()` on the main thread, to build the downsampled
  envelope — and this happens synchronously as part of the same `_load_path` call
  that constructed the buffer, before playback starts, so it never overlaps with
  the audio thread reading the *same* buffer object (a new `AudioBuffer` is
  constructed per load; there is no shared mutable buffer across tracks).

---

## 5. End-to-end walkthroughs

### Loading a track (double-click in the library)

1. `LibraryPanel.itemDoubleClicked` → `LibraryPanel._on_item_double_clicked` emits
   `trackChosen(Track)` (`ui/panels/library_panel.py:37-40`).
2. `MainWindow._on_track_chosen(track)` → `MainWindow._load_path(track.source_path)`
   (`ui/main_window.py:113-116`).
3. `_load_path` calls `self._app.load(path)` (`main_window.py:119`) — **synchronous,
   main thread** — which runs the full chain in §2/§3: hash file, check segments
   exist, decode audio, construct `PositionTracker`/`PlaybackEngine`/
   `SegmentLookup`/`MidiDispatcher`.
4. Back in `_load_path`: `self._state.load_track(file_hash)` — `EditorState`
   fetches segments from `store` and emits `StateEvent("loaded")`.
5. `EditorStateBridge` translates that to the Qt `loaded` signal →
   `MainWindow._on_state_loaded` populates `SegmentTableModel` and
   `SegmentOverlay`.
6. `self.waveform.load(self._app.buffer)` draws the envelope; `transport.set_duration_ms(...)`
   sets the seek slider range; `transport.set_enabled_playback(True)` enables the
   transport buttons; `self._pos_timer.start()` begins polling position.

At no point in this chain does the code check whether a track was already loaded
and playing — see §6.

### Pressing Play

1. `TransportControls.playClicked` → `PlaybackController.play()` →
   `Application.play()` (`application.py:75-77`).
2. `self.dispatcher.start()` spawns the `MidiDispatcher` daemon thread (§3, row 3).
3. `self.engine.play()` constructs (first time only) and starts the
   `sd.OutputStream`, which spawns the PortAudio native callback thread (§3, row 2).
4. From this point, three threads run concurrently: main (UI + `_pos_timer` at
   `_POS_TIMER_MS = 50`), audio callback, and MIDI dispatcher — each reading/writing
   the shared `PositionTracker` under its lock, independently of each other.

---

## 6. Known architectural gaps (as-built facts, not yet fixed)

These are the code-level causes behind the bugs logged in
`docs/debug/List of known errors`. Documented here for reference; **no fix has
been applied for either** — that's a follow-up decision pending review of this
document.

**Gap A — no teardown before reload.** `Application.load()` (`application.py:59-73`)
reassigns `self.buffer`/`self.tracker`/`self.engine`/`self.lookup`/`self.dispatcher`
to new objects without first calling `self.stop()`. If a track is playing (or
paused with an open stream) when a second track is loaded:
- The **old** `PlaybackEngine`'s `sd.OutputStream` is not stopped or closed. Since
  the stream holds a bound-method reference to the old engine's `_callback`, the
  old engine object stays alive and its native audio thread keeps pulling frames
  from the *old* `AudioBuffer` and pushing them into the singleton `viz.queue` —
  now shared with whatever the new engine also pushes there.
- The **old** `MidiDispatcher` thread (if playback had been started) is not
  stopped/joined. It keeps polling the *old* `PositionTracker` and can still send
  Program Changes through the singleton `port`, racing with the new track's
  dispatcher on the same MIDI channel.
- `Application.engine`/`Application.dispatcher` now point at the *new* track's
  objects, so `PlaybackController`'s play/pause/stop/seek calls act only on the
  new track — the old, orphaned engine has no remaining external handle and
  cannot be paused or stopped through the UI. This is consistent with both the
  "choppiness clears up after loading a different song" report (two audio streams
  briefly contending for the output device) and the "going back to the first song
  starts a new instance instead of controlling the original" report (the original
  engine was never the one still running — a second orphaned instance was).

**Gap B — synchronous load blocks the UI thread.** As detailed in §3, `load()`'s
full-file SHA-256 hash plus the full `librosa.load(sr=None, mono=False)` decode
run inline on the Qt main thread with no worker thread or process behind them.
For a multi-minute track this is a multi-second block of the GUI event loop —
consistent with the "takes multiple seconds to pull up a song" report.
