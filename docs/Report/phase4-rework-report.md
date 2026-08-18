# Phase 4 Rework Report — M0 + O1 + O2

_Written 2026-07-14, covering commit `4bdf312` ("Phase 4 M0+O1+O2: lifecycle fixes, mode shell, playlists/queue, segment timeline")._

**What this document is.** A record of what the Phase 4 rework changed and *why* — the reasoning behind each change, not a file-by-file inventory. It supersedes `phase4-ui-architecture.md` as a description of current behaviour; that document is retained unedited as a snapshot of the pre-rework design (M1–M3) and of the diagnosis that led here. Where the two disagree, this one is current.

**Why the rework happened.** Three user-reported bugs (`docs/debug/user-reported-errors-phase4.md`) turned out to be symptoms of two structural problems in the M1–M3 UI: no teardown of per-track playback objects on reload, and a main thread doing far too much work. Fixing them properly meant restructuring the shell, so the fixes were bundled with the Home/Analysis/Output overhaul (`docs/plans/phase4-overhaul-plan.md`).

---

## 1. The three reported bugs

| # | Symptom (user's words) | Cause |
|---|---|---|
| 1 | "loading a song can often be slow… taking multiple seconds" | `Application.load()` ran the full-file SHA-256 hash and the entire `librosa` decode **inline on the Qt main thread** — the GUI event loop was blocked for the duration. |
| 2 | "audio is often really choppy… tends to go away after loading up a different song" | Two independent causes, see below. |
| 3 | "going back to the initial song… starts a different instance… audio overlayed rather than pausing" | `Application.load()` **never tore down the previous track's runtime graph.** |

Bug 3 is the clearest, so take it first — it also explains half of bug 2.

---

## 2. M0 — lifecycle fixes

### 2.1 Teardown before rebuild (bugs 2 and 3)

The old `Application.load()` reassigned `buffer`, `tracker`, `engine`, `lookup`, and `dispatcher` to brand-new objects without stopping the old ones. `Application.stop()` existed and was correct — it was simply never called.

The consequence is worse than a leak. The old `PlaybackEngine`'s `sd.OutputStream` holds a bound-method reference to `_callback`, so the old engine **stays alive and keeps playing** off its own `AudioBuffer`, on its own PortAudio thread. Meanwhile `Application.engine` now points at the *new* track's engine, so the transport buttons act only on the new one — the still-audible old stream has no remaining handle and cannot be paused or stopped. That is exactly bug 3: audio from the first track overlaying the second, uncontrollable. The orphaned `MidiDispatcher` thread likewise kept polling its stale `PositionTracker` and racing the new dispatcher to send Program Changes on the same singleton MIDI port.

It also explains part of bug 2: two `sd.OutputStream`s open on one output device contend for it, which crackles.

**The fix** splits `load()` into two methods with a deliberate ordering constraint:

```python
def attach(self, file_hash, buffer, name=""):
    if not self.store.get_segments(file_hash):   # 1. validate FIRST
        raise NoSegmentsError(...)
    self.stop()                                  # 2. then tear down the old graph
    self.buffer = buffer                         # 3. then wire the new one
    ...
```

Validation comes before teardown so that **a failed load leaves the currently playing track untouched** — loading a track with no analysed segments no longer silently kills playback.

### 2.2 Decode off the main thread (bug 1)

`load()` is split at the boundary of what is safe to run off-thread:

- **`Application.decode(path)`** — hash + full `librosa` decode. Touches no DB, no Qt, no playback state. Safe on a worker thread.
- **`Application.attach(hash, buffer)`** — validate, teardown, rewire. Main thread only, because it reads the sole SQLite connection and mutates the live runtime graph.

`LoadWorker(QThread)` runs `decode()`; `MainWindow._on_load_decoded` calls `attach()` back on the main thread when the `decoded` signal arrives. The split is what makes this safe: the worker never touches the DB, so the "one SQLite connection, main thread only" invariant is preserved without locking.

`MainWindow._load_path` also guards re-entry (`if self._load_worker is not None: return`), so hammering tracks in the library can't spawn overlapping loads.

`play()`/`pause()`/`seek()` gained `None` guards — with async loading there is now a real window where the UI is alive but no track is attached.

---

## 3. ISSUE-005 — the real cause of the choppiness

Teardown fixed the *overlapping-streams* half of bug 2. Choppiness persisted, and an earlier mitigation attempt (blocksize 1024→2048, `latency="high"`, timer 33→50 ms) had already failed to shift it.

**The controlled test that settled it.** After the O1 mode shell shipped, `_on_pos_tick` only moves the playhead when Analysis is the visible mode. The user then observed that **playback from Home is clean, and choppiness appears only while Analysis is on screen.** Same engine, same blocksize, same dispatcher — the only variable is whether the pyqtgraph repaint runs.

**The mechanism.** `WaveformView.set_playhead_ms()` → `InfiniteLine.setValue()` moves a `QGraphicsItem`, invalidating a scene region spanning the plot's full height. Qt therefore re-invoked `paint()` on every item intersecting it: two 3000-point envelope curves, a `FillBetweenItem`, and one `LinearRegionItem` per segment (16 on the test track). **Every one of those `paint()` methods is Python code inside pyqtgraph**, so 20 times a second the main thread ran a burst of Python-level painting while holding the GIL.

`PlaybackEngine._callback()` is also a Python function, invoked from PortAudio's native thread. It must acquire the GIL to run and cannot preempt the main thread mid-bytecode. It does almost no work — one numpy slice copy — it simply could not get **scheduled**. Missed deadline → output underrun → crackle. This is GIL scheduling, not CPU throughput: more cores cannot help, because both threads need the same lock.

**Why buffering was the wrong fix.** The failed attempt assumed the deadline was too *tight* and bought more headroom. Doubling headroom changed nothing — which is itself the proof: if 2× buffering doesn't help, the GIL stall is not small jitter you can buffer around, it is comparable to the block period itself. The dominant term was the *cost and frequency of the paint work*. (The same attempt's "skip repaint if position unchanged" was a no-op during playback by construction — the position changes every tick while playing.)

**The fix: `SegmentTimeline` replaces the waveform entirely.** The user had independently judged the waveform unnecessary for the app, so the pyqtgraph `WaveformView` and `SegmentOverlay` were deleted rather than optimised. `views/segment_timeline.py` is a plain `QWidget` attacking all three levers:

1. **Cost per repaint** — one `paintEvent`, one `fillRect` per segment plus a playhead rect, all thin wrappers over C++ (PySide6 releases the GIL around C++ calls). No scene graph, no per-item Python `paint()`, no 3000-point path.
2. **Repaint frequency** — pixel-gated: `update()` only when the playhead crosses a **new pixel column** (~4–5×/sec over a 4-minute track, down from 20).
3. **Repaint occurrence** — mode-gated: no playhead work at all unless Analysis is visible.

The timeline is also *better UI* for the actual job — colour-coded tone bands are what the correction workflow needs; the envelope was decoration.

**Hygiene, not cause:** the engine's `viz_queue` is now `None`. With no consumer (spectrum is deferred to O4) the bounded queue sat full, so every audio callback paid a `queue.Full` raise/catch for nothing. This was *never* the cause — it was mode-independent and so could not explain a mode-dependent symptom.

**Reverted 2026-07-14:** `blocksize` back to 1024 and `latency="high"` dropped. They were residue from the failed fix, and they are not free — `PositionTracker` reports the **end** of the block just queued, so the reported position already leads the audible position by one block plus device latency, and `MidiDispatcher` adds 75 ms lookahead on top. Bigger blocks push amp preset changes earlier relative to the tone boundary you actually hear. Live re-test passed: no choppiness, preset switches land on time by ear.

Full write-up: `docs/debug/ISSUE-005-choppy-playback-gil-contention.md`.

---

## 4. O1 — the mode shell

`MainWindow` became a shell rather than a screen: a mode sidebar + `QStackedWidget` (Home / Analysis / Output) + a persistent transport bar that survives mode switches. Each mode lives in `ui/modes/` and owns its own widgets and internal wiring, talking to the shell **only through signals and plain methods** — no mode touches `EditorState` or `Application` directly.

Two payoffs beyond navigation. It made the ISSUE-005 discriminating test possible for free (playhead work is now gated on the visible mode, which is what exposed the pyqtgraph repaint as the variable). And it keeps per-mode complexity from accreting back into `MainWindow`, which had already become the single place where every widget, timer, and signal met.

---

## 5. O2 — playlist-first Home and the queue

**Schema v7 and v8** (migrations, additive — the live `library.db` migrated v6→v8 with all 129 corrected segments intact):
- v7: `segments_calibration` — a lazy pre-edit snapshot of `segments`, reserved for O3's correction work. It exists so the destructive DELETE-then-INSERT re-analysis path can stay as-is without losing a pristine copy for calibration.
- v8: `playlists`, `playlist_tracks` (+ `Track.analysed_at`).

**Home** is now the entry point: a virtual "Library" playlist plus user playlists with CRUD/membership, and a song table (Title / Artist / Date added / Progress). Double-clicking a song plays it and seeds the queue from its playlist — and **stays in Home**, rather than throwing the user into an editor they didn't ask for.

**`QueueState`** is Qt-free and unit-tested standalone (`tests/test_queue_state.py`), following the same pattern as `EditorState`: shuffle with a stable unshuffle, repeat modes, move/remove/play-next. A `QueueStateBridge` adapts it to Qt signals. The queue sidebar, transport prev/next, and track-finished auto-advance all read from it.

---

## 6. Status

- 221 tests passing, ruff clean (194 at `4bdf312`; O3 work in progress since).
- Known errors 1–3 verified fixed by the user. ISSUE-005 audibly confirmed resolved in Analysis mode.
- Live `library.db` migrated v6→v8 with segments and corrections intact.

**Verified live (2026-07-14):** carry_on_my_wayward_son played end-to-end with real MIDI after the `blocksize`/`latency` revert — no choppiness (including with Analysis mode on screen, the previously failing condition), preset switches on time by ear.
