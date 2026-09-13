# Save State — Guitar Helper
_Last updated: 2026-08-25_

Status: **Phases 1-3 DONE. Phase 4 M0-M3 + O1-O4 DONE.** **Gates 1-6 complete** — Gate 5 (packaging) landed 2026-08-25. 631 tests passing, ruff-clean. Remaining before public release: ffmpeg bundling decision, live rig verification.

## READ FIRST
- **`docs/plans/mvp-implementation-plan.md`** — master execution plan: 6 gates, ordering rationale, how each is accomplished. Everything below is context for it.
- `docs/Report/mvp-readiness-review.md` — findings + quality grades (Reliability C+, Robustness B-, Maintainability A-, SOLID A-, Testability A, Deployability D).
- `docs/Report/Guitar_Performance_Assistant_Report_v0.4.md` — as-built architecture. **v0.3 is superseded** and describes components never built (IRenderer/SpectrumAnalyzer/LyricsParser).
- `docs/architecture/ARCHITECTURE.md` — component map, 17 numbered design decisions (D1-D17), threading contract.

## Gate 5 (Packaging) — RESOLVED 2026-08-25
Spec + as-built record: **`docs/new feature specs/Packaging_and_Update_Strategy.md`** (§11 is the as-built section — read it before touching the build).
- PyInstaller **onedir** (not onefile), Inno Setup **per-user** install, `installer/` dir (NOT `packaging/` — shadows the PyPI package on sys.path).
- **Separation tier IS bundled (reversed 2026-08-26).** 847 MB frozen tree -> **247 MB installer**, 852 MB installed. Build ~15 min, installer compile ~6 min.
- `AppConfig.resolve()` gained a frozen branch: default root is `%LOCALAPPDATA%\GuitarHelper` when `sys.frozen`, CWD otherwise. `resource_path()` is separate and NOT reachable via `--root`.
- Updater: `guitar_helper/update/` (Qt-free) + `ui/update_worker.py` + `ui/dialogs/update_prompt.py`. HTTPS-only, SHA-256 verified before launch, Inno `/SILENT` handoff.
- `installer/release.ps1` builds installer + `manifest.json` together; digest always computed from the artefact, never hand-written.
- **Separation was NOT bundled originally; that decision was reversed 2026-08-26.** Separation is the product, not an add-on: build_pipeline always uses AudioSeparator and archetypes.json is calibrated on stem features, so a build without it analyses nothing. The ~2 GB that justified excluding it is a CUDA torch install; the CPU build is 498 MB.
- **ffmpeg NOT bundled - it is a user prerequisite.** The local msys2 build is GPL (--enable-gpl --enable-libx264) and dynamically linked. Installer's consent page documents it and reports live PATH state.
- **`check_ffmpeg()` is now BLOCK, not WARN.** audio_separator's `Separator.__init__` calls `check_ffmpeg_installed()`, which RAISES when absent - so the separator cannot be constructed and NOTHING can be analysed, .wav included. Reverses the old "ffmpeg is a WARN that becomes a per-file rejection" rule.
- **diffq stub vendored** at `installer/stubs/diffq.py` (on the spec's pathex). It previously lived only in the dev venv, so a fresh checkout could not build a working app.
- **Model weights are a build-time hard requirement.** Spec raises SystemExit if absent rather than shipping a bundle that imports fine and cannot separate. `GUITAR_HELPER_MODEL_DIR` overrides the default source dir (C:/tmp/audio-separator-models).
- `default_model_dir()` in source_separator.py is the single resolution point, shared with `environment.check_model_weights()`; frozen -> `resource_path("models")`.

### Packaging facts that cost time — do not rediscover
- **`--selftest` exists and must be run against every build** (`GuitarHelper.exe --selftest`). A frozen build that merely *launches* proves nothing: librosa/numba/sklearn aren't touched until first analysis. It forces every deferred import AND a real `FeatureExtractor.extract` (numba JIT-compiles on first CALL, not import).
- **Frozen windowed build has `sys.stdout is None`.** Anything that prints (audio-separator's tqdm/logging) raises `AttributeError` on `None.write`. `installer/launcher.py` redirects both streams to `%LOCALAPPDATA%\GuitarHelper\guitar-helper.log` before importing anything. **Read that log when a build misbehaves — there is no console.**
- **numba writes its JIT cache next to the compiled module** = read-only install dir. `installer/runtime_hook.py` sets `NUMBA_CACHE_DIR` before librosa imports.
- **`.ps1` files must be pure ASCII.** PowerShell 5.1 reads them as ANSI without a BOM; an em dash in a comment is a parse error.
- **Never pipe PyInstaller through `2>&1` in PS 5.1** — it logs to stderr, and redirecting wraps each line in an ErrorRecord and reports failure on a successful build.
- **winget installs Inno Setup per-user** at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`, not Program Files.
- **Do NOT verify a bundle by listing `_internal/`.** Pure-Python packages (requests, mutagen)
  live inside the PYZ archive embedded in the exe, not as loose directories, so `ls _internal/`
  shows them as "missing" when they are present. Only packages with binaries or data files
  (certifi, charset_normalizer, soundfile) appear there. **`--selftest` is the only valid check.**
- **`torch.distributed` and `torch.testing` CANNOT be excluded** - torch/__init__.py imports both unconditionally. Excluding them to save space breaks `import torch` and takes audio_separator with it; the app still launches and fails only on the first song. Caught by --selftest. `torch._inductor`, `torch._dynamo`, `torch.utils.tensorboard`, `torch.utils.benchmark`, `torch.include` ARE safe (verified via sys.modules after a bare `import torch`).
- **An Inno `[Code]` line may not start with `#`** - ISPP reads it as a preprocessor directive. A bare `#13#10 +` continuation is a compile error; prefix it `'' + #13#10 +`.
- `.gitignore` has `*.spec`; `!installer/GuitarHelper.spec` overrides it.

## MVP blockers (remaining)
- **ffmpeg is a user prerequisite (decided 2026-08-26, not a blocker any more).** Installer documents it; preflight blocks with the winget command. Revisit by shipping a static LGPL ffmpeg if it proves a support burden.
- **Unsigned installer** — SmartScreen warns on first run. Cost/reputation decision, deferred.
- Update flow never exercised against a real GitHub release; unit-tested with a faked transport only.

## Gate 3 (ISSUE-007) — RESOLVED
GUI ingestion path built end-to-end: `AnalysisWorker` (QThread), progress dialogs, Home "Add songs…" button + playlist right-click, File menu entry. `delete_track` on `ITrackEditor` role (mixed into `IAppStore`, not `ISegmentStore`); Home right-click "Remove from library" warns with corrected-segment count, caches stem (D5).

## Gate 4 — RESOLVED
**H1:** NullMidiPort fallback + persistent "MIDI disabled" banner (no loopMIDI required). **H2:** `attach()` failures caught broadly, previous track keeps playing. **H3:** correction CLI saves via atomic `apply_edits` (snapshots first). **H5:** playlist creation catches `sqlite3.IntegrityError` only.

## Gate 6 — RESOLVED
README.md, user-guide.md, Help menu entry.
**LICENCE RESOLVED 2026-09-13: the project is GPL-3.0-or-later, not MIT.**
- Forced by what the binary bundles: mutagen is GPL-2.0-or-later (copyleft). GPLv2 was NOT an option - `requests` is Apache-2.0 (incompatible with v2) and PySide6 offers only LGPL-3.0/GPL-2.0/GPL-3.0, no LGPL-2.1. So v3 is the only version that works.
- `LICENSE` is the verbatim GPL-3.0 text. `COPYRIGHT` holds the notice, the version reasoning, and the source offer. `THIRD-PARTY-NOTICES.md` lists every bundled component.
- Installer shows the licence (`LicenseFile`) + `InfoBeforeFile=COPYRIGHT`, and installs all three at the app root AND in `_internal` (About dialog links them via `resource_path`).
- About dialog carries the GPLv3 5(d) Appropriate Legal Notices (copyright, no warranty, redistribution right, licence link).
- Update manifest gained `source_url` (HTTPS-validated); the update prompt always states the licence and shows the source link, because an update conveys a binary too.
- `--selftest` now fails if LICENSE/COPYRIGHT/THIRD-PARTY-NOTICES are missing from a bundle.
- htdemucs_6s: Demucs is MIT and permits redistribution, but neither its LICENSE nor README says anything about the *weights* as distinct from code; upstream calls htdemucs_6s experimental. Recorded in THIRD-PARTY-NOTICES.md.
- **OPEN: the source-offer contact in `COPYRIGHT` is a placeholder (`CONTACT-NOT-YET-SET`).** Must be a real address before the binary goes to anyone else, OR make the repo public (satisfies GPLv3 6(d) and drops the 3-year obligation).

## Resolved (ISSUE-006/008, B1, schema v10)
- **B1 — `save_segments` not transactional.** RESOLVED: wrapped in transaction; `EditorState.save()` uses atomic `apply_edits()`.
- **ISSUE-006** — preset-map divergence. RESOLVED: option A — `presets.user_modified` column, v10 migration reconciles non-user-modified rows, partial UNIQUE index. Live DB migrated intact.
- **ISSUE-008** — stem-cache poisoning. RESOLVED: atomic publish + completion manifest + validation on hit; sampled digest mandatory.
- **Schema downgrade hole** (found during Gate 5). `_apply_migrations` used `range(current+1, _CURRENT+1)`, which is EMPTY when the DB is ahead of the code — an older build silently opened a newer DB. Now `SchemaTooNewError`, raised BEFORE `executescript`, so a too-new DB is left byte-identical. Also: `init_db` now auto-backs-up to `library.db.bak-v{from}-to-v{to}-{stamp}` before any migration (sqlite backup API, WAL-safe), keeping the last 5. Pruning matches only that name pattern, so hand-made `.bak-*` snapshots survive.

## Facts NOT derivable from code
- **D3 open / needs live rig test.** "Pause playback while analysing" ships defaulted OFF; by-ear test decides the default. Do not settle from code — ISSUE-005 lesson.
- **LICENSE unconfirmed.** MIT / Aryan Kumar / 2026 picked by default; needs deliberate decision before Gate 5 redistributes Demucs weights (whose licence terms haven't been checked).
- **`run_playback.py` and `measure_latency.py` check `app.midi_available` post-H1**, exit 2 if absent. Matters for `measure_latency`: measuring dispatch latency against a no-op port prints meaningless numbers.
- **MIDI port selection is startup-only.** With the banner, users will expect to fix a missing loopMIDI without relaunching; port picker in Output mode is the fix, not in Wave 2 scope.
- **`Stage` is StrEnum but `Enum.__hash__` hashes the name**, misses on dict lookup by the `.value` string arriving over Qt signal. Key such dicts by `.value`.
- Restore point: `mvp-wave2-restore-point` tag, plus `.backup-mvp-wave2/library.db.bak` (gitignored).

## Product decisions
- **No songs/DB ship.** Library user-supplied; only `archetypes.json` ships.
- **Separation MANDATORY.** Stem first, never analyse full mix. Justification: stem vs full-mix over 123 labelled segments = accuracy .821 vs .463; macro-F1 ~0.69 (stem) vs worse (full-mix). Violations: `pipeline.py:43` defaults to `NullSeparator`, `--no-separate` on CLIs.

## Two models — do not conflate
- `archetypes.json` = **3 KB**, 5 tones x 24 floats, OURS, adapts via `run_calibrate`, ships in git.
- `htdemucs_6s` = **52 MB** third-party pretrained SEPARATION net, fixed, never calibrated, NOT in the repo. Build sources it from `C:/tmp/audio-separator-models/` (override: `GUITAR_HELPER_MODEL_DIR`) and BUNDLES it; the frozen app reads it from `_internal/models`. The `C:/tmp` original is still an unsafe location for the source copy.
- `archetypes.json` is useless without htdemucs running first (it is calibrated on stem features). The "~2 GB" figure is torch+onnxruntime (the code that runs the net), not the weights.

## CRITICAL incident — calibration labels lost & protected
- The original 129 manual labels were WIPED by a re-analysis (2026-06-19 reset `manually_corrected`->0). Unrecoverable: OneDrive version history had only post-wipe copies; `*.db` is gitignored. Only `archetypes.json` survived (in git).
- Root-cause fix in place: re-analysis raises `ManualCorrectionsExistError` unless `--discard-corrections`; `run_batch` reports SKIP. **B1 above is the remaining hole in this protection.**

## Current calibration state
- `archetypes.json`: clean, edge, overdrive, crunch, metal (mean-based). `ambient` removed.
- EXP-001 LOOCV macro-F1 ~0.69 (was 0.434). Per-tone F1: metal .89, crunch .82, overdrive .71, other .69, clean .56, edge .48.
- `overdrive` has only 5 labelled segments — least robust; label more + re-run `run_calibrate`.
- Live `library.db`: **9 tracks, 123 segments, 123 corrections, 1 playlist, schema v10.** All rows migrated; no duplicates; unique index on `pc_number >= 0`.
- Calibration statistic = **mean** (EXP-001: clean-data LOOCV prefers mean .693 vs median .660). Median is the fallback if outlier contamination returns.

## MIDI preset mapping (presets table = source of truth; never hardcode)
- FRESH DB / `_DEFAULT_PRESETS`: clean=PC0, edge=PC1, overdrive=PC2, crunch=PC3, metal=PC4, other=-1 (no dispatch).
- **RETRACTION (2026-08-04) of the 2026-08-03 "CORRECTION".** That note called clean0/crunch1/metal2/edge3/overdrive4 wrong. It was NOT wrong — it is the real layout of any DB created before the gain-ramp reorder (ISSUE-006). Both lines were true, of different databases; the 08-03 check was made against a fresh DB and over-generalised.
- The live DB reads the fresh order despite having been migrated v6->v8->v9, so those rows were changed after migration — almost certainly via the O4 preset table (the only caller of `save_preset`). Do NOT assume other DBs match.

## Resolved issues (detail in docs/debug/)
- **ISSUE-004** VST3 does not deliver raw Program Change to hosted plugins (Steinberg architecture, not our bug). **Use Nolly VST2 or standalone.** Deployment constraint — must be in the README.
- **ISSUE-005** pyqtgraph 20Hz scene repaint starved the audio callback of the GIL. Waveform deleted; `SegmentTimeline` repaints only per pixel-column. pyqtgraph now UNUSED (still in requirements — drop it). Restore point: tag `pre-issue-005-fix`.
- ISSUE-001 (auto-k), ISSUE-002 (dual normalisation), ISSUE-003 (edge/crunch overlap -> stem separation).

## Latency — knob DONE, measurement still pending
- Model: `optimal_lookahead = L_chain + poll_wait - L_out`. ONE global knob; boundaries are seconds apart.
- `L_out` (tracker lead) + the 75ms lookahead STACK, so PCs likely fire EARLY, not late. The correction is probably *less* lead.
- `L_chain` (loopMIDI->host->plugin audible switch) is NOT measurable from Python — supplied by ear.
- NEXT: run `python -m guitar_helper.measure_latency <track> --seconds 30` on the rig, type the result into Output -> Dispatch offset. **The `settings` table is EMPTY — the knob has never been set.** NOT a release blocker (75ms default works and is user-tunable).
- Unclaimed side-win: `poll_interval_s` 50ms -> 15ms halves poll jitter, negligible cost.

## Environment
- Python 3.14.3, Windows 11; venv `.venv/`. ffmpeg 8.0.1 installed. Primary target format: iTunes .m4a.
- `audio-separator` 0.44.2 (htdemucs_6s), separate heavy install — see `requirements-separation.txt` for the py3.14 `--no-deps` + `diffq` stub procedure.
- `python-rtmidi`: no cp314 wheel -> meson source build; runtime-only, not in CI. **Resolved by freezing** — the bundle ships the compiled `.pyd`, so end users need no MSVC.
- Build toolchain: `requirements-packaging.txt` (pyinstaller) + `winget install --id JRSoftware.InnoSetup -e`.
- `ruff.toml` target-version=py312 (py314 bump deferred). CI: windows-latest, py3.14, ruff + pytest.
- **The repo is inside OneDrive.** `stems/` (400 MB) syncs pointlessly, and Files On-Demand can dehydrate a stem so it `exists()` with the right size but needs a network fetch. Move the dev stems dir out. Volume is 95% full (54 GB free).
- Measured: analysis of a 189s track = 10.7s with the stem cached; separation itself is minutes and dominates. The `_stack_raw` duplication is 0.77s of that (7%) — NOT the "~2x" an earlier draft claimed.

## Deferred / out of MVP scope
- QOL pass (`docs/new feature specs/Phase_4_QOL_changes.md`) — min:sec + typed boundary entry, modified-segment indicator, unmerge/split, multi-select, reload-on-save (edits need a song switch today: `SegmentLookup` snapshots at construction, D6), icon transport, right-click playlist add, preferences tab, help tab.
- Review findings M1-M7 · `MainWindow` SRP extraction (441 lines) · move `ui/editor/` to fix the Tier1->Tier3 import · stem-cache eviction · lyrics (`guitar_helper/lyrics/` is an EMPTY package) · spectrum (re-adds pyqtgraph + viz_queue) · cover art · queue persistence · `ambient` re-add · more overdrive labels.
- Hygiene: 5 stray `.db` files at root, `batch_reanalysis.log`, `print_db.py`.
- Accepted gap (user-confirmed): the dirty-guard applies only to playlist Analyze/Prev/Next, not to queue Prev/Next or Home double-click.

