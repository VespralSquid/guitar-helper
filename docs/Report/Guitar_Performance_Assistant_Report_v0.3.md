# Guitar Performance Assistant
## Project Design Report — Version 0.3
### Full Offline Architecture & Three-Tier Design

**Version:** 0.3 — Full Offline Redesign  
**Date:** 2026-06-11  
**Author:** Aryan  
**Supersedes:** v0.2 (Guitar_Performance_Assistant_Report_v0.2.md)

---

## 1. Project Evolution Summary

This document is the third iteration of the Guitar Performance Assistant design. Each version was driven by a concrete finding or decision — not scope creep. The table below traces every major change and its cause.

| Version | Key Decision | Cause |
|---|---|---|
| v0.1 | Spotify Audio Analysis API as offline segmentation source | Initial design |
| v0.1 | Spotify Web API polling (~500ms) as runtime position source | Initial design |
| v0.2 | Replace Spotify Audio Analysis API with librosa + local audio | Spotify revoked endpoint access for new apps (late 2024 policy change) |
| v0.2 | Spotify Web API retained for runtime polling | Not yet addressed |
| v0.3 | Remove Spotify Web API entirely; replace with sounddevice local playback | Eliminates polling latency, lookahead complexity, OAuth dependency, and internet requirement. Also provides raw audio samples — prerequisite for visual tier. |
| v0.3 | Replace Spotify `track_id` (UUID) with SHA-256 file hash | No Spotify to issue IDs; file hash is content-addressable and stable |
| v0.3 | Add Presentation Tier (visualizations, lyrics, waveform) | Extended vision: shareable app for other guitarists |

---

## 2. Problem Statement (Unchanged)

Playing electric guitar alongside a backing track presents a recurring friction point: songs transition between tonally distinct sections (e.g. a clean acoustic intro into a distorted metal body), but guitar rigs do not switch presets automatically. The guitarist must either wait idle through sections where their tone does not fit, or manually reduce volume and play acoustically — both compromising the performance.

**Goal:** Build a system that detects which section of a locally-stored song is currently playing, determines its tonal character, and automatically loads the appropriate amp/effects preset into the guitarist's rig with no manual intervention.

The system has a secondary goal: serve as a polished, distributable desktop application that bedroom guitarists can share with friends and family — featuring real-time audio visualizations and synchronized lyrics.

---

## 3. Why All External Dependencies Were Removed

### 3.1 Spotify Audio Analysis API (removed in v0.2)
The endpoint `/audio-analysis/{track_id}` was restricted for new apps in late 2024. Access was confirmed unavailable. Replacement: librosa running on locally-owned audio files.

### 3.2 Spotify Web API runtime polling (removed in v0.3)
Even after removing the Analysis API, the v0.2 design retained Spotify's `/me/player/currently-playing` endpoint to get `position_ms` at runtime. This introduced three structural problems:

1. **500ms polling interval** — created a timing gap that made the lookahead logic a fragile workaround rather than a real solution.
2. **No raw audio access** — Spotify's API gives position but not audio samples. Real-time visualizations (spectrum analyzer, waveform) require the live audio buffer, which polling cannot provide.
3. **Unnecessary external dependency** — the songs are already local files. Querying an internet API to find out where in a local file you are is architecturally incoherent.

Replacement: `sounddevice` streams playback from the local file via a callback. The callback's frame cursor gives exact `position_ms` with sub-millisecond precision, and the live audio chunk is available for visualizations at no additional cost.

---

## 4. Three-Tier Architecture

The system is organized into three tiers with strict boundaries. Higher tiers depend on lower tiers; lower tiers have no knowledge of higher tiers.

```
┌─────────────────────────────────────────────────────┐
│  TIER 3 — PRESENTATION                              │
│  PySide6 UI · pyqtgraph · Lyrics · Visualizations  │
└──────────────────────┬──────────────────────────────┘
                       │ reads position_ms, audio chunks
                       │ reads segment labels
┌──────────────────────▼──────────────────────────────┐
│  TIER 2 — PLAYBACK ENGINE                           │
│  sounddevice · PositionTracker · MidiDispatcher     │
└──────────────────────┬──────────────────────────────┘
                       │ reads segment-tone maps
┌──────────────────────▼──────────────────────────────┐
│  TIER 1 — ANALYSIS PIPELINE                         │
│  librosa · FeatureExtractor · Segmenter · SQLite    │
└─────────────────────────────────────────────────────┘
```

**Tier 1** runs offline, once per song.  
**Tier 2** runs during a performance session.  
**Tier 3** runs alongside Tier 2 and is entirely optional — removing it leaves Tiers 1 and 2 fully functional.

---

## 5. Tier 1 — Analysis Pipeline

### 5.1 Purpose
Runs once per song before any live performance. Reads a local audio file, extracts spectral features, identifies segment boundaries, auto-labels each segment's tone, and persists the result to SQLite for runtime lookup.

### 5.2 Data Flow
```
Audio file
  → AudioLoader        — soundfile.read() → numpy array y, sample rate sr
  → FeatureExtractor   — librosa features per frame → feature matrix (F × T)
  → Segmenter          — agglomerative clustering → boundary timestamps []
  → ToneClassifier     — mean feature vector per segment → tone_label + confidence
  → SegmentRepository  — persist to SQLite keyed by file_hash
  → SegmentCorrectionTool — manual label review and override (CLI)
```

### 5.3 Components

**AudioLoader**  
Responsibility: load an audio file into memory as a numpy float32 array.  
Returns: `(y: np.ndarray, sr: int, duration_ms: int)`  
Handles: WAV and FLAC natively via `soundfile`; MP3 via `pydub` + ffmpeg.  
Also computes the SHA-256 file hash used as the database primary key.

**FeatureExtractor**  
Responsibility: compute frame-level spectral features from raw audio samples.  
Features computed per frame (~10ms hop):

| Feature | Function | Role |
|---|---|---|
| Spectral Flatness | `librosa.feature.spectral_flatness` | Primary clean/distorted discriminator (0=tonal, 1=noise-like) |
| Zero-Crossing Rate | `librosa.feature.zero_crossing_rate` | Secondary discriminator; high in distorted signals |
| RMS Energy | `librosa.feature.rms` | Loudness; separates quiet acoustic from loud electric sections |
| Spectral Centroid | `librosa.feature.spectral_centroid` | Frequency center of mass; rises with distortion |
| Spectral Contrast | `librosa.feature.spectral_contrast` | Peak/valley ratio; high in tonal signals |
| MFCCs (13 coeff.) | `librosa.feature.mfcc` | Timbre fingerprint; effective classifier input |

Returns: `feature_matrix: np.ndarray` of shape `(n_features × n_frames)`

**Segmenter**  
Responsibility: find natural boundary timestamps in the feature matrix.  
Uses `librosa.segment.agglomerative(feature_matrix, k)` to cluster frames into `k` segments. `k` is either supplied per-song or estimated via a novelty curve (peak detection on the self-similarity matrix diagonal).  
Returns: `boundaries: list[int]` (millisecond timestamps)

**ToneClassifier**  
Responsibility: assign a tone label and confidence score to each segment.  
Compares each segment's mean feature vector to archetype profiles (see Section 5.4).  
Returns: `list[tuple[str, float]]` — `(tone_label, confidence)` per segment.

**AnalysisPipeline**  
Responsibility: orchestrate AudioLoader → FeatureExtractor → Segmenter → ToneClassifier → SegmentRepository.  
This is the only component that knows the full pipeline order. Individual components do not reference each other.

**SegmentRepository**  
Responsibility: all read/write operations on the SQLite database.  
Implements `ISegmentStore`.

**SegmentCorrectionTool**  
Responsibility: present auto-generated segment labels to the user for review; write corrections back to the database.  
Implementation: CLI-first (fastest to build; internal tooling).  
Prints each segment with its timestamps, auto-label, and confidence. User can retype the label. Sets `manually_corrected = 1` and `confidence = 1.0` for corrected rows.

### 5.4 Tone Archetype Profiles

Approximate profiles for auto-labelling. Thresholds require calibration against 5–10 target songs.

| Tone | Flatness | ZCR | RMS | Centroid |
|---|---|---|---|---|
| `clean` | Low | Low | Low–Med | Low |
| `crunch` | Medium | Medium | Medium | Medium |
| `heavy` | High | High | High | High |
| `ambient` | Low–Med | Very Low | Low | Low |
| `other` | — | — | — | — |

`other` is a catch-all for unclassifiable segments. Runtime behavior: hold current preset, log the segment. Does not dispatch MIDI.

---

## 6. Tier 2 — Playback Engine

### 6.1 Purpose
Runs during a performance session. Drives local audio playback, tracks position with sub-millisecond precision, queries the segment database, and dispatches MIDI preset changes to Ableton.

### 6.2 Data Flow
```
User selects file
  → AudioBuffer        — loads full file into RAM
  → PlaybackEngine     — sounddevice stream, audio callback loop
      callback fires (~10ms):
        → advances cursor
        → puts (cursor, chunk) into VisualizationBridge queue  [→ Tier 3]
  → PositionTracker    — cursor / sr * 1000 = position_ms
  → SegmentLookup      — SELECT segment WHERE file_hash=? AND start_ms<=? AND end_ms>?
  → MidiDispatcher     — if tone changed: send Program Change via IMidiPort
  → MidiPort           — mido → loopMIDI → Ableton → Neural DSP
```

### 6.3 Thread Model

```
Audio Thread (sounddevice)
  runs at OS audio priority
  must be non-blocking
  writes: cursor, audio chunks → VisualizationBridge queue

MIDI/Position Thread
  reads position_ms from PositionTracker
  queries SegmentLookup
  drives MidiDispatcher
  ~50ms polling interval (10× finer than old 500ms Spotify poll; no API cost)

UI Thread (PySide6 event loop)
  reads from VisualizationBridge queue
  updates all visual components
  handles user input (play/pause/seek)
```

The audio callback is kept strictly non-blocking. All DB access, MIDI dispatch, and UI updates happen outside it.

### 6.4 Components

**AudioBuffer**  
Responsibility: load the full audio file into a numpy array at song load time.  
Holds: `data: np.ndarray`, `sr: int`, `duration_ms: int`.  
Decoupled from playback — the engine reads from this buffer; it does not know about files.

**PlaybackEngine**  
Responsibility: manage the sounddevice output stream and audio callback.  
Exposes: `play()`, `pause()`, `seek(ms)`, `stop()`.  
The callback reads a chunk from AudioBuffer at the current cursor position and advances the cursor. It also enqueues `(cursor, chunk)` for Tier 3.

**PositionTracker**  
Responsibility: convert the audio cursor to milliseconds.  
`position_ms = (cursor / sr) * 1000`  
Also computes `ms_until_next_boundary(position_ms, segments)` for lookahead MIDI pre-triggering.

**SegmentLookup**  
Responsibility: return the segment active at a given position.  
Depends on: `ISegmentStore`  
Query: `SELECT ... WHERE file_hash=? AND start_ms<=? AND end_ms>? ORDER BY start_ms DESC LIMIT 1`

**MidiDispatcher**  
Responsibility: compare the active segment's tone to the last dispatched tone; send a Program Change only when the tone changes.  
Depends on: `IMidiPort` (not mido directly — see Section 8 DIP)  
Lookahead: fires when `position_ms >= segment.start_ms - lookahead_ms`. Lookahead can now be small (50–100ms) since position is exact — no polling gap to compensate for.

**MidiPort / IMidiPort**  
Responsibility: send a MIDI Program Change to the loopMIDI virtual cable.  
`MidoPort` is the concrete implementation. `MockMidiPort` is used in tests.

### 6.5 MIDI Implementation Notes (unchanged from v0.1)

- Preset switching: Program Change messages (`status 0xC0`). 0-indexed (preset 1 = program 0).
- Effect toggling (reverb etc.): Note On messages. Neural DSP does not respond to CC in Ableton.
- Ableton MIDI track Monitor must be set to "In".
- loopMIDI port must have "Track" enabled in Ableton Preferences → Link/MIDI.
- loopMIDI must be running before the Python process starts (startup check required).

---

## 7. Tier 3 — Presentation

### 7.1 Purpose
Visual and interactive layer. Entirely optional — Tiers 1 and 2 are fully functional without it. Can be iterated on independently without touching audio or MIDI logic.

### 7.2 Components

**MainWindow** (PySide6 QMainWindow)  
Responsibility: top-level window, layout management, lifecycle management of all sub-views.

**TransportControls**  
Responsibility: play/pause/seek/volume widgets. Drives PlaybackEngine via signals.

**WaveformView** (pyqtgraph PlotWidget)  
Responsibility: display a scrolling pre-computed waveform overview with segment boundary markers.  
Data source: pre-computed from AudioBuffer at load time — not recomputed per frame.

**SpectrumAnalyzer** (pyqtgraph BarGraphItem)  
Responsibility: real-time FFT bar display of the current audio chunk.  
Data source: `VisualizationBridge` queue — audio chunk from PlaybackEngine callback.  
Computation: `np.fft.rfft(chunk)` → bin magnitudes → bar heights. ~60fps update.

**LyricsDisplay** (PySide6 QLabel)  
Responsibility: display current lyric line, fade previous/next lines.  
Data source: parsed LRC file keyed to `position_ms`.  
LRC format: `[mm:ss.xx] lyric text` — plain text, no library needed.

**SegmentOverlay**  
Responsibility: display current tone label (`CLEAN` / `CRUNCH` / `HEAVY`) and upcoming transition countdown.  
Data source: active segment from `SegmentLookup`, `position_ms` from `PositionTracker`.

**VisualizationBridge**  
Responsibility: thread-safe queue connecting the audio callback thread to the UI thread.  
Implementation: `queue.Queue` — put from audio thread, get in UI QTimer tick.

**LyricsParser**  
Responsibility: parse `.lrc` files into a sorted list of `(timestamp_ms, text)` tuples.  
`get_current_line(position_ms)` → binary search → current lyric string.

---

## 8. SOLID Design Principles

The architecture is structured to follow SOLID principles explicitly. This section documents how each principle is applied and where it matters.

### 8.1 Single Responsibility Principle
Each class has exactly one reason to change.

| Class | Single responsibility | Changes only when... |
|---|---|---|
| `AudioLoader` | Load audio files | Audio loading mechanics change |
| `FeatureExtractor` | Compute spectral features | Feature set or computation changes |
| `Segmenter` | Find boundary timestamps | Segmentation algorithm changes |
| `ToneClassifier` | Assign tone labels | Classification logic or archetypes change |
| `MidiDispatcher` | Send MIDI on tone change | MIDI dispatch logic changes |
| `LyricsParser` | Parse LRC files | LRC format parsing changes |
| `SpectrumAnalyzer` | Render spectrum bars | Spectrum visualization changes |

`AnalysisPipeline` is the one place that knows the pipeline order. Each step is ignorant of the others.

### 8.2 Open/Closed Principle
Open for extension, closed for modification.

**ToneClassifier is abstract:**
```python
class BaseToneClassifier(ABC):
    @abstractmethod
    def classify(self, feature_vector: np.ndarray) -> tuple[str, float]:
        ...

class ThresholdClassifier(BaseToneClassifier): ...   # current implementation
class KNNClassifier(BaseToneClassifier): ...         # future: k-nearest neighbours
class MLClassifier(BaseToneClassifier): ...          # future: trained model
```
Adding a new classifier never touches existing code.

**IRenderer is abstract:**
```python
class IRenderer(ABC):
    @abstractmethod
    def update(self, chunk: np.ndarray, position_ms: int) -> None: ...
```
`SpectrumAnalyzer`, `WaveformView`, `LyricsDisplay`, `SegmentOverlay` all implement `IRenderer`. New visualizations are added by implementing this interface — nothing else changes.

### 8.3 Liskov Substitution Principle
Subtypes are fully substitutable for their base type.

- Any `BaseToneClassifier` subclass can replace another in `AnalysisPipeline` without breaking it.
- Any `IRenderer` can be added to or removed from the render loop without affecting others.
- `MockMidiPort` fully substitutes `MidoPort` in test contexts — `MidiDispatcher` behaves identically.

LSP is enforced by keeping abstract interfaces narrow and not leaking implementation details into base class contracts.

### 8.4 Interface Segregation Principle
No component is forced to depend on interfaces it does not use.

| Interface | Methods | Used by |
|---|---|---|
| `IPlaybackPositionProvider` | `get_position_ms() -> int` | `MidiDispatcher`, `SegmentOverlay`, `LyricsDisplay` |
| `IAudioChunkProvider` | `get_latest_chunk() -> np.ndarray` | `SpectrumAnalyzer` |
| `ISegmentStore` | `get_segment(hash, ms)`, `save_segments(hash, segments)` | `SegmentLookup`, `SegmentRepository` |
| `IMidiPort` | `send_program_change(channel, program)` | `MidiDispatcher` |

`MidiDispatcher` depends on `IPlaybackPositionProvider` — it does not see the audio buffer or the UI. `SpectrumAnalyzer` depends on `IAudioChunkProvider` — it does not see the MIDI system or the database. Each component has a minimal, purpose-fit view of the system.

### 8.5 Dependency Inversion Principle
High-level modules depend on abstractions. Concrete dependencies are injected at the composition root.

```
MidiDispatcher      → IMidiPort           (not mido.open_output())
SegmentLookup       → ISegmentStore       (not sqlite3.connect())
AnalysisPipeline    → ISegmentStore       (not sqlite3.connect())
SpectrumAnalyzer    → IAudioChunkProvider (not PlaybackEngine directly)
ToneClassifier use  → BaseToneClassifier  (not ThresholdClassifier directly)
```

The `Application` class (composition root) constructs all concrete objects and injects them:
```python
db = SQLiteSegmentStore("library.db")
midi = MidoPort("loopMIDI Port 1")
classifier = ThresholdClassifier(archetypes)
dispatcher = MidiDispatcher(position_provider=tracker, segment_store=db, port=midi)
```

This means every component is independently testable by injecting mocks.

---

## 9. Data Model

### 9.1 Schema

```sql
CREATE TABLE tracks (
    file_hash    TEXT PRIMARY KEY,      -- SHA-256 of audio file bytes
    filename     TEXT NOT NULL,         -- display name / relative path
    title        TEXT,
    artist       TEXT,
    duration_ms  INTEGER NOT NULL,
    analysed_at  TEXT NOT NULL          -- ISO 8601 UTC
);

CREATE TABLE segments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash           TEXT NOT NULL REFERENCES tracks(file_hash),
    start_ms            INTEGER NOT NULL,
    end_ms              INTEGER NOT NULL,
    tone_label          TEXT NOT NULL,  -- clean | crunch | heavy | ambient | other
    preset_name         TEXT NOT NULL,  -- exact Neural DSP preset name
    confidence          REAL NOT NULL,  -- 0.0 (ambiguous) to 1.0 (certain)
    manually_corrected  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_seg_lookup ON segments(file_hash, start_ms, end_ms);

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
```

**Key change from v0.1/v0.2:** `track_id TEXT` (Spotify UUID) replaced by `file_hash TEXT` (SHA-256). Content-addressable — stable across renames if the file bytes are unchanged. No Spotify dependency.

### 9.2 Runtime Query

```sql
SELECT *
FROM segments
WHERE file_hash = ?
  AND start_ms <= ?
  AND end_ms   > ?
ORDER BY start_ms DESC
LIMIT 1;
```

`ORDER BY start_ms DESC` ensures a deterministic result if segments overlap (possible after manual correction). Executes in < 1ms locally.

### 9.3 Confidence Computation

`confidence = 1.0 - normalized_euclidean_distance(segment_mean_features, nearest_archetype)`  
Range: 0.0 (maximally ambiguous) to 1.0 (exact archetype match).  
Overridden to `1.0` when `manually_corrected = 1`.  
Segments with `confidence < 0.5` are flagged for review in the Segment Correction Tool.

---

## 10. Technology Stack

### 10.1 Python Libraries

| Library | Tier | Role |
|---|---|---|
| `librosa` | 1 | Feature extraction, structural segmentation |
| `soundfile` | 1 + 2 | WAV/FLAC audio loading |
| `pydub` | 1 + 2 | MP3 decoding (requires ffmpeg system install) |
| `numpy` | 1 + 3 | Array operations, FFT for spectrum analyzer |
| `sqlite3` | 1 + 2 | Database persistence (stdlib) |
| `sounddevice` | 2 | Audio output stream + callback |
| `mido` | 2 | MIDI message construction |
| `python-rtmidi` | 2 | Low-level MIDI backend for mido |
| `PySide6` | 3 | UI framework (LGPL, distributable) |
| `pyqtgraph` | 3 | Real-time spectrum + waveform rendering |

### 10.2 System Dependencies

| Dependency | Required by | Notes |
|---|---|---|
| ffmpeg | pydub / audioread | MP3 loading; one-time OS install |
| loopMIDI | mido / python-rtmidi | Virtual MIDI cable on Windows; free |
| PortAudio | sounddevice | Bundled in sounddevice Windows wheels; no separate install |

### 10.3 Infrastructure & DAW (unchanged)

| Component | Tool | Notes |
|---|---|---|
| DAW | Ableton Live 12 Lite | 2-track limit; sufficient (1 audio + 1 MIDI) |
| Amp sim | Neural DSP plugin | Receives Program Change; loads presets by index |
| Audio interface | Focusrite Scarlett Solo 3rd Gen | Guitar → computer |

---

## 11. Risk Table

| Risk | Severity | Mitigation |
|---|---|---|
| Spotify Audio Analysis API unavailable | **Resolved** | Replaced with librosa (v0.2) |
| Spotify runtime API dependency | **Resolved** | Replaced with sounddevice local playback (v0.3) |
| librosa features affected by full mix (drums, bass) | Medium | Distorted guitar sections typically co-occur with loud drums — features compound in the right direction. Validate against 5–10 songs; manual correction covers edge cases. |
| Segment boundary accuracy | Medium | Agglomerative clustering finds natural boundaries. Segment Correction Tool provides label and boundary override. |
| MP3 loading requires ffmpeg | Low | One-time OS install; document in setup guide |
| `other` tone at runtime | Low | Hold current preset; log segment; do not dispatch MIDI |
| loopMIDI not running at launch | Low | Python startup check; raise descriptive error. Configure for Windows auto-start in performance context. |
| Neural DSP CC messages non-functional in Ableton | High (if misunderstood) | Use Program Change for presets; Note On for effect toggles. Verified. |
| Song not in database at runtime | Medium | Hold current preset; log missing hash |
| Audio file moved/renamed | Low | file_hash is content-based; renaming doesn't break lookup. Moving the file requires re-registering the path. |

---

## 12. Open Questions

| # | Question |
|---|---|
| 1 | Segment Correction Tool: CLI only, or embed in the Tier 3 UI (scrub + preview)? |
| 2 | Tone label taxonomy: finalize set and confirm 1:1 mapping to Neural DSP preset slot indices |
| 3 | Threading model: asyncio vs. threading for MIDI/position watcher thread |
| 4 | librosa feature threshold calibration: run against 5–10 target songs, tune archetype profiles |
| 5 | Lyrics source: manual `.lrc` files only, or integrate LrcLib free API as optional fetch? |
| 6 | Distribution: PyInstaller `.exe` for Windows; document ffmpeg + loopMIDI as prerequisite installs |
| 7 | Segment Correction Tool boundary editing: allow users to adjust `start_ms`/`end_ms`, or labels only? |

---

*Guitar Performance Assistant — Project Design Report v0.3*  
*2026-06-11*
