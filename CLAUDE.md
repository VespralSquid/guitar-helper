# Guitar Performance Assistant — Project Brief

## What this project is
Fully offline Python desktop app: analyses locally-stored audio files (librosa) → stores segment/tone data (SQLite) → plays audio (sounddevice) → fires MIDI Program Change messages at tone boundaries → displays a PySide6 UI with a segment timeline, segment editor, and MIDI dispatch log.

Three tiers: Analysis (offline batch) → Playback + MIDI (runtime) → Presentation (PySide6 UI).

## Current status
- Phases 1–3 complete: DB layer, analysis pipeline, stem separation, calibration, playback engine, MIDI dispatch, CI/CD on GitHub (`VespralSquid/guitar-helper`, private)
- Phase 4 milestones M0–M3 + O1–O4 complete. Remaining Phase 4 work is the QOL pass (`docs/new feature specs/Phase_4_QOL_changes.md`)
- Pre-MVP: see `docs/Report/mvp-readiness-review.md` for open release blockers, and `docs/architecture/ARCHITECTURE.md` for the as-built map

## Multi-Agent Routing

When delegating work, use the `Agent` tool with the `model` parameter:

| Task | Model | When |
|---|---|---|
| Phase planning, feasibility analysis, architectural tradeoffs | `opus` | Any multi-step design decision |
| Code implementation, debugging, refactoring, code review | `sonnet` | Default — handle directly |
| Short doc updates (SAVE_STATE.md, docstrings, boilerplate) | `haiku` | No reasoning required |
| Calibration analysis (interpreting segment output, tuning classifier) | `opus` | Judgment-heavy interpretation |

**Parallel spawning:** When tasks are independent (e.g. plan a phase AND update docs), spawn both subagents in the same message so they run concurrently.

## Coding Conventions
- No comments unless the WHY is non-obvious; never describe what the code does
- No `Co-Authored-By` lines in git commits
- Parameterized SQL only — never f-strings in queries
- Audio callback (`sounddevice`) must be non-blocking — no DB, MIDI, or UI calls inside it
- MIDI dispatcher runs on its own thread; reads position via thread-safe `PositionTracker`
- `presets` table is single source of truth for tone→PC mapping; never hardcode PC numbers
- `other` tone at runtime: hold current preset, do not dispatch, log the event
- `INSERT OR IGNORE` / upsert when re-seeding; never DROP and recreate on startup

## MIDI Preset Mapping
| Tone | PC |
|---|---|
| clean | 0 |
| edge | 1 |
| overdrive | 2 |
| crunch | 3 |
| metal | 4 |
| other | -1 (no dispatch) |

PC order is a deliberate clean->metal gain progression (reordered from the original clean/crunch/metal/edge/overdrive layout to smooth the ramp). `ambient` is deferred — removed from presets/classifier (schema v4). Re-add ambient later as a custom preset on a free PC.

**KNOWN DEFECT (ISSUE-006, OPEN) — this table describes a *freshly created* database only.** `_seed()` runs only when `schema_version` is absent, and three of the five historical changes to `_DEFAULT_PRESETS` shipped without a migration. A database created before the gain-ramp reorder keeps `clean=0, crunch=1, metal=2, edge=3, overdrive=4`; one created before Phase 2 has no `edge` row at all, which makes `edge` segments unstorable (FK violation). Verify against the live DB before trusting either layout. Full analysis, repro and fix options: `docs/debug/ISSUE-006-preset-map-migration-divergence.md`.

## Debug Documentation

When a non-trivial bug is discovered and fixed during development, create a report in `docs/debug/`:

- File naming: `ISSUE-NNN-short-slug.md` (e.g. `ISSUE-001-segmentation-k1.md`)
- Use the `/quick-docs` command to delegate report writing to Haiku
- Provide Haiku with: component, symptom, root cause, what was tried, the fix, and current status

**Multi-agent routing for debug docs:**
| Task | Model |
|---|---|
| Writing a new debug report | `haiku` — invoke via `/quick-docs` |
| Diagnosing the root cause | `sonnet` — handle directly |
| Interpreting calibration output to guide a fix | `opus` — invoke via `/phase-plan` |

## Save State

Before context compaction, update `SAVE_STATE.md`. Use `/quick-docs` to delegate to Haiku.

**Rules — concision over grammar:**
- Sacrifice grammar. No full sentences where a bullet works.
- No redundancy with what is already in the code or git history.
- Include: completed phases/tasks, pending work, critical facts NOT derivable from code (verbal decisions, env quirks, active bugs, MIDI mapping corrections).
- Omit: file-by-file descriptions of working code, commit history, anything a future agent can read from the codebase.
- Keep the whole file under ~100 lines.

**What belongs in SAVE_STATE.md:**
- Phase completion status
- Active bugs / ISSUE-NNN blockers
- Decisions made verbally that aren't in the report or code
- Environment facts (ffmpeg absent, ruff target-version quirk, etc.)
- What was wrong in a previous save state that has since been corrected

## Key File Map
```
guitar_helper/
  db/
    schema.py       — init_db(), DDL, _DEFAULT_PRESETS seed
    interfaces.py   — ISegmentStore ABC, Segment + Preset dataclasses
    repository.py   — SQLiteSegmentStore
  analysis/
    audio_loader.py — AudioLoader.load() (hash+duration), load_mono() (librosa)
    feature_extractor.py — 24-feature matrix, min-max normalised
    segmenter.py    — agglomerative boundary detection, novelty-curve auto-k
    tone_classifier.py   — BaseToneClassifier ABC + ThresholdClassifier
    pipeline.py     — AnalysisPipeline.run() orchestrator
  correction/
    cli.py          — SegmentCorrectionTool (interactive label + boundary editing)
  playback/
    audio_buffer.py — decoded float32 (frames, channels) in RAM
    playback_engine.py   — sounddevice stream; non-blocking callback
    position_tracker.py  — lock-guarded frame cursor -> ms
    segment_lookup.py    — bisect over a construction-time snapshot
    midi_dispatcher.py   — own thread; on-change-only PC dispatch
    dispatch_log.py      — bounded thread-safe decision log (send/hold/unmapped/gap)
    latency_probe.py     — opt-in instrumentation; None in production
  midi/             — IMidiPort, MidoPort, MockMidiPort
  ui/
    app.py, main_window.py — composition of the Qt shell
    modes/          — home.py (playlists), analysis.py (editor), output.py (MIDI)
    state/          — editor_state.py, queue_state.py (Qt-FREE), state_bridge.py
    editor/         — validation.py, merge.py, preset_validation.py (Qt-FREE)
    views/          — segment_timeline.py (replaced the pyqtgraph waveform)
    panels/         — queue_sidebar.py
    models/         — qt_adapters.py (QAbstractTableModel adapters)
    theme.py, transport.py, controllers.py, load_worker.py
  application.py    — composition root; per-track lifecycle (decode/attach)
  config.py         — one root -> db/library/stems/archetypes paths
  lyrics/           — EMPTY PACKAGE. LrcParser/LrcLibClient not implemented (deferred)
```

Full as-built map, design decisions and threading model: `docs/architecture/ARCHITECTURE.md`.

## Environment
- Python 3.14.3, Windows 11
- venv at `.venv/`; activate before running anything
- ffmpeg NOT installed — WAV/FLAC/OGG work; MP3/AAC blocked until ffmpeg is added
- ruff linting: `python -m ruff check guitar_helper/`
- CI: GitHub Actions on `windows-latest`, runs ruff + pytest on push to main
- Do not mention the use of claude and advertising phrases such as "co-authored by Claude" and similar.