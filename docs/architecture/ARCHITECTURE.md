# Guitar Helper — Architecture & Design Decision Inventory

_Compiled 2026-08-04 from the code at `de249c7`. This is the as-built map: what
exists, what depends on what, and which decisions are load-bearing. Where it
disagrees with `docs/Report/Guitar_Performance_Assistant_Report_v0.3.md`, this
document is current — v0.3 is the June design intent, not the shipped system._

---

## 1. Tier map (as built)

```
TIER 3 — PRESENTATION                     guitar_helper/ui/
  app.run() → MainWindow → {HomeMode, AnalysisMode, OutputMode}
              + TransportControls + QueueSidebar
  Qt-free core: EditorState, QueueState, editor/{validation,merge,preset_validation}
  Qt bridges:  EditorStateBridge, QueueStateBridge, qt_adapters (table models)
                          │ signals only; modes never touch Application
┌─────────────────────────▼───────────────────────────────┐
COMPOSITION ROOT                          guitar_helper/application.py
  Application: owns store, port, dispatch_log, dispatch_offset_ms (singletons)
               builds per-track graph in attach(): buffer/tracker/engine/
               lookup/dispatcher
                          │
TIER 2 — PLAYBACK + MIDI                  guitar_helper/playback/, midi/
  AudioBuffer → PlaybackEngine (sounddevice callback) → PositionTracker
  PositionTracker → MidiDispatcher (own thread) → IMidiPort → loopMIDI
  SegmentLookup (in-memory bisect snapshot) · DispatchLogBuffer · latency_probe
                          │ reads segments + presets
TIER 1 — ANALYSIS                         guitar_helper/analysis/, db/
  AudioLoader → ISourceSeparator → FeatureExtractor → Segmenter
              → BaseToneClassifier → ISegmentStore (SQLite)
  correction/cli.py (CLI editor) · run_calibrate (archetype fitting)
```

Tier 3 is genuinely optional: `run_playback.py` and `run_analysis.py` exercise
Tiers 1+2 with no Qt import.

## 2. Component inventory

| Module | Responsibility | Key collaborators |
|---|---|---|
| `db/schema.py` | DDL, `_CURRENT_VERSION=10`, migration chain, preset seed, `reconcile_presets` | — |
| `db/interfaces.py` | 6 role ABCs + `Segment/Preset/Track/Playlist` dataclasses | — |
| `db/repository.py` | `SQLiteSegmentStore` — the only SQL in the app (except `run_calibrate`) | `IAppStore`, `IPlaylistStore` |
| `analysis/audio_loader.py` | hash + duration (no decode), `load_mono`, `decode` | soundfile, librosa, pydub |
| `analysis/feature_extractor.py` | 24-feature matrix, two normalisations | librosa |
| `analysis/segmenter.py` | boundary detection, cosine-novelty auto-k, min-length merge | librosa, scipy |
| `analysis/tone_classifier.py` | archetype nearest-neighbour + RMS silence gate | `archetypes.json` |
| `analysis/source_separator.py` | htdemucs_6s guitar stem, hash-keyed cache | audio-separator (lazy import) |
| `analysis/pipeline.py` | the only module that knows pipeline order; corrections guard | all of the above |
| `analysis/file_locator.py` | re-find a moved track by content hash | AudioLoader |
| `playback/audio_buffer.py` | decoded float32 (frames, channels) in RAM | AudioLoader |
| `playback/playback_engine.py` | sounddevice stream; non-blocking callback | AudioBuffer, PositionTracker |
| `playback/position_tracker.py` | lock-guarded frame cursor → ms | — |
| `playback/segment_lookup.py` | bisect over a construction-time snapshot | `ISegmentReader` |
| `playback/midi_dispatcher.py` | own thread; on-change-only PC dispatch | `IPresetStore`, `IMidiPort` |
| `playback/dispatch_log.py` | bounded thread-safe decision log (send/hold/unmapped/gap) | — |
| `playback/latency_probe.py` | opt-in instrumentation; `None` in production | — |
| `midi/` | `IMidiPort` + `MidoPort` (fail-fast) + `MockMidiPort` | mido/rtmidi |
| `ui/state/editor_state.py` | Qt-free edit session: working list, pending, dirty | `ISegmentStore` |
| `ui/state/queue_state.py` | Qt-free queue: order, shuffle, repeat | — |
| `ui/editor/` | Qt-free validate/apply for segments and presets | — |
| `ui/main_window.py` | shell: modes, load pipeline, timers, queue/playlist sync | Application, EditorState |
| `application.py` | composition root + per-track lifecycle | everything |
| `config.py` | one `root` → db/library/stems/archetypes paths | — |

## 3. Load-bearing design decisions

| # | Decision | Rationale | Consequence if changed |
|---|---|---|---|
| D1 | SHA-256 file content is the track identity | No Spotify IDs; survives rename | Moving a file needs re-locate, not re-analysis |
| D2 | `presets` table is the sole tone→PC source | Never hardcode PC numbers | Any PC edit must go through `save_preset` |
| D3 | `other` = PC −1 = hold, never dispatch | A silent amp beats a wrong preset | Encoded in schema seed *and* `preset_validation` |
| D4 | Audio callback is strictly non-blocking | ISSUE-005: GIL starvation caused choppiness | No DB/MIDI/UI/queue work in `_callback` |
| D5 | Dispatcher thread never touches SQLite | sqlite3 connections aren't thread-shareable | `set_pc_map` takes a dict; `SegmentLookup` snapshots |
| D6 | `SegmentLookup` snapshots at construction | Per-tick DB query has no place in dispatch | Segment edits need a reload to take effect (known QOL gap) |
| D7 | Two feature normalisations | Segmentation wants intra-song variation; classification wants absolute character | Archetypes are only valid in clf-space |
| D8 | Calibration statistic = mean | EXP-001: clean-data LOOCV prefers mean (F1 .693 vs .660) | Switch to median only if outliers return |
| D9 | Analysis runs on the separated guitar stem | Boundaries track guitar tone, not the full mix | Archetypes must be calibrated on stems too |
| D10 | Re-analysis refuses to overwrite manual corrections | The 129-label wipe incident | `--discard-corrections` is the only override |
| D11 | Merge is physical; `segments_calibration` keeps the pre-edit snapshot | Calibration needs original granularity | `ensure_calibration_copy` is lazy + idempotent |
| D12 | Qt-free state core + Qt bridge adapters | State is unit-testable without a QApplication | `editor_state.py`/`queue_state.py` must never import PySide6 |
| D13 | `IAppStore = ISegmentStore + ISettingsStore`, kept split | Folding settings into `ISegmentStore` broke test doubles — the exact ISP violation the O3 split prevents | New roles get new ABCs, not new methods on old ones |
| D14 | Decode on `LoadWorker` thread, `attach` on main thread | Bug 1 (freeze) + bug 3 (orphaned streams) | `decode()` must stay DB/Qt/playback-free |
| D15 | One global dispatch offset, persisted in `settings` | Boundaries are seconds apart — no per-segment correction needed | Model: `L_chain + poll_wait − L_out` |
| D16 | Waveform removed; `SegmentTimeline` repaints per pixel-column | ISSUE-005 | pyqtgraph is now unused |
| D17 | VST2/standalone amp, not VST3 | VST3 doesn't deliver raw PC to plugins (ISSUE-004) | Not a code defect; a deployment constraint |

## 4. Threading model (as built)

| Thread | Owns | Reads | Writes | Rule |
|---|---|---|---|---|
| PortAudio callback | — | `AudioBuffer.data` | `_frame`, `PositionTracker._cursor`, `last_tracker_lead_ms` | No DB/MIDI/UI/alloc |
| `MidiDispatcher` | `_last_tone`, `_last_pc` | `PositionTracker`, `SegmentLookup`, `_pc_by_tone` dict | MIDI port, `DispatchLogBuffer` | Never touches SQLite |
| `LoadWorker` (QThread) | — | file bytes | — | Never touches DB or playback objects |
| `AnalysisWorker` (QThread) | one `AnalysisPipeline` | audio files, stem cache, librosa/torch | stem cache | Never touches SQLite, Qt widgets or playback objects |
| Qt main | Application graph, the single sqlite3 connection, all widgets | everything | store, dispatcher config | Sole SQLite owner |

`AnalysisWorker` follows the `LoadWorker` shape one tier up: `AnalysisPipeline.analyse()`
computes on the worker and `persist()` runs in the main-thread `fileDone` slot, so the
sole-SQLite-owner rule survives ingestion. `precheck()` reads the store and therefore
also stays on the main thread — the worker is handed its skip/analyse decisions
pre-computed. Cancellation is polled at stage boundaries only; a separation already in
flight runs to completion because `Separator.separate()` exposes no abort hook.

Synchronisation primitives: `PlaybackEngine._lock`, `PositionTracker._lock`,
`DispatchLogBuffer._lock`, `Samples._lock`, `MidiDispatcher._stop` (Event).
`lookahead_ms` and `_pc_by_tone` rely on atomic attribute rebinding by design.

## 5. Data model (schema v10)

`tracks` · `segments` · `segments_calibration` · `presets` · `playlists` ·
`playlist_tracks` · `settings` · `schema_version`

- FKs ON. `segments.tone_label → presets.tone_label` — deleting or missing a
  preset row makes segments with that tone unstorable.
- `presets.pc_number` carries a **partial UNIQUE index** (`idx_presets_pc`, over
  `pc_number >= 0`) as of schema v10, so `other`'s -1 sentinel can repeat while
  every dispatched PC stays distinct. `ui/editor/preset_validation.validate_pc`
  still checks it first so the UI reports a clash instead of an IntegrityError.
- `presets.user_modified` marks rows the Output panel has edited; the v10
  migration reconciles only rows where it is 0 (ISSUE-006).
- Migrations `_MIGRATIONS[2..9]`, applied in order. They mutate preset rows by
  hand and do **not** re-converge on `_DEFAULT_PRESETS` (see the MVP review).

## 6. Extension points (Open/Closed in practice)

| Seam | ABC | Existing implementations |
|---|---|---|
| Tone classification | `BaseToneClassifier` | `ThresholdClassifier` |
| Stem separation | `ISourceSeparator` | `NullSeparator`, `AudioSeparator` |
| MIDI output | `IMidiPort` | `MidoPort`, `MockMidiPort` |
| Persistence roles | `ITrackCatalog`, `ISegmentReader`, `ISegmentEditor`, `IPresetStore`, `ISettingsStore`, `IPlaylistStore` | `SQLiteSegmentStore` |
| App settings | key/value `settings` table | a new knob is a new key, never a migration |

## 7. Known accepted gaps (deliberate, recorded)

- Dirty-guard covers playlist Analyze/Prev/Next only — not queue Prev/Next or
  Home double-click (user-confirmed scoping decision).
- Segment edits require a track reload to reach the dispatcher (D6).
- Latency measurement not yet run on the rig; offset knob shipped, value not set
  (`settings` table is empty in the live DB).
- `overdrive` has only 5 labelled segments — weakest archetype.
- Lyrics tier (`guitar_helper/lyrics/`) is an empty package; spectrum view deferred.
