# Guitar Performance Assistant — Project Brief

## What this project is
Fully offline Python desktop app: analyses locally-stored audio files (librosa) → stores segment/tone data (SQLite) → plays audio (sounddevice) → fires MIDI Program Change messages at tone boundaries → displays a PySide6 UI with a segment timeline, segment editor, and MIDI dispatch log.

Three tiers: Analysis (offline batch) → Playback + MIDI (runtime) → Presentation (PySide6 UI).

## Current status
- Phases 1–3 complete: DB layer, analysis pipeline, stem separation, calibration, playback engine, MIDI dispatch, CI/CD on GitHub (`VespralSquid/guitar-helper`, private)
- Phase 4 milestones M0–M3 + O1–O4 complete. Remaining Phase 4 work is the QOL pass (`docs/new feature specs/Phase_4_QOL_changes.md`)
- **MVP Wave 1 (2026-08-18) complete:** Gates 1–2 partially done. ISSUE-006 (preset reconciliation), ISSUE-008 (stem-cache validation), and B1 (atomic writes) resolved. Schema v10. ISSUE-007 groundwork done (Qt-free tier split).
- **MVP Wave 2 complete:** Gate 3 GUI ingestion, `delete_track`, Gate 4 robustness, Gate 6 docs.
- **Gate 5 packaging complete (2026-08-25):** PyInstaller onedir + Inno Setup per-user installer + SHA-256-verified manifest updater. 634 tests passing. Separation bundled 2026-08-26. See `docs/new feature specs/Packaging_and_Update_Strategy.md` §11 (as-built) before touching the build.

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

As of schema v10, this table describes both fresh and migrated databases. The `_migrate_v9_to_v10` migration reconciles non-user-modified rows against `_DEFAULT_PRESETS`, adds the missing `edge` row for old databases, and enforces a partial UNIQUE index on `pc_number >= 0`. Rows with `user_modified = 1` (set by the Output panel) are preserved unchanged. See `docs/debug/ISSUE-006-preset-map-migration-divergence.md` for the full reconciliation story.

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
- Environment facts (ruff target-version quirk, rtmidi source build, etc.)
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
  update/           — Qt-free update core: manifest, verified download, installer handoff
  application.py    — composition root; per-track lifecycle (decode/attach)
  config.py         — one root -> db/library/stems/archetypes paths; frozen-aware
                      (`is_frozen`, `user_data_dir`, `resource_path`)
  lyrics/           — EMPTY PACKAGE. LrcParser/LrcLibClient not implemented (deferred)
installer/          — build system (NOT `packaging/`: that name shadows the PyPI package)
  launcher.py       — frozen entry point; stream redirect, crash dialog, --selftest
  runtime_hook.py   — redirects NUMBA_CACHE_DIR before librosa imports
  GuitarHelper.spec — PyInstaller onedir spec
  GuitarHelper.iss  — Inno Setup, per-user install + component consent page
  stubs/diffq.py    — vendored stub; audio-separator's Demucs imports it eagerly
  build.ps1         — freeze only
  release.ps1       — freeze -> installer -> manifest.json
```

## Packaging rules
- **`GuitarHelper.exe --selftest` against every build.** A frozen build that launches proves
  nothing: librosa/numba/sklearn aren't imported until first analysis. It forces the imports
  AND a real feature extraction (numba JIT-compiles on first *call*).
- **No console in a frozen build** — `sys.stdout` is None and stream output goes to
  `%LOCALAPPDATA%\GuitarHelper\guitar-helper.log`. Read it when a build misbehaves.
- **`.ps1` files must be pure ASCII** (PowerShell 5.1 reads them as ANSI without a BOM).
- **Never pipe PyInstaller through `2>&1` in PS 5.1** — it logs to stderr, and the redirect
  reports failure on a successful build.
- Read-only bundled assets go through `resource_path()`, never `Path(__file__).parents[n]`.
  Writable user data goes through `AppConfig`. The two must not share a root.
- **The separation tier IS bundled** (torch CPU + onnxruntime + audio-separator + htdemucs_6s).
  Reversed 2026-08-26: separation is the product, not an add-on. 847 MB tree -> 247 MB installer.
- **`torch.distributed` and `torch.testing` cannot be excluded** — `torch/__init__.py` imports
  both unconditionally, so excluding them breaks `import torch` and audio-separator with it.
- **ffmpeg is a user prerequisite, not bundled.** `check_ffmpeg()` is a BLOCK: audio-separator's
  `Separator.__init__` raises without it, so nothing can be analysed at all.
- An Inno `[Code]` line may not begin with `#` — ISPP reads it as a preprocessor directive.
- **Build output goes outside OneDrive** (`%LOCALAPPDATA%\GuitarHelperBuild`). Building into the
  repo makes OneDrive re-sync 847 MB per build and can fail it with `WinError 5`.
- **The published manifest must never carry a UTF-8 BOM.** `json.loads` rejects one, and the
  shipped 1.0.0 client decodes strict utf-8, so a BOM makes updates invisible to it.
- **The project is GPL-3.0-or-later** (since 2026-09-13), forced by bundling mutagen
  (GPL-2.0-or-later). GPLv2 is not available: `requests` is Apache-2.0 and PySide6 offers
  no LGPL-2.1. `LICENSE` must stay the verbatim GPL-3.0 text; see `COPYRIGHT`.
- Anything that conveys a binary must carry the source offer: the installer shows the
  licence, and the update manifest carries `source_url`. `--selftest` fails if the legal
  files are missing from a bundle.

Full as-built map, design decisions and threading model: `docs/architecture/ARCHITECTURE.md`.

## Environment
- Python 3.14.3, Windows 11
- venv at `.venv/`; activate before running anything
- ffmpeg 8.0.1 installed — WAV/FLAC/OGG/MP3/AAC all supported
- ruff linting: `python -m ruff check guitar_helper/`
- CI: GitHub Actions on `windows-latest`, runs ruff + pytest on push to main
- Do not mention the use of claude and advertising phrases such as "co-authored by Claude" and similar.