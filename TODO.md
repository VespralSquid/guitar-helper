# TODO

Working checklist. **`docs/plans/mvp-implementation-plan.md` is the authority** on
scope and ordering — this file is the scannable view, not a second source of truth.
If the two disagree, the plan wins.

_Updated 2026-08-19. Gate 1 done; Gate 2 done except `delete_track`; Gate 3 groundwork done.
459 tests, CI green._

---

## Next — Wave 2: the last release blocker (ISSUE-007)

A user still cannot add a song without a terminal. Everything here is serial: one
component depending on all three of last night's lanes.

- [ ] `delete_track(file_hash)` — one transaction over `segments`, `segments_calibration`,
      `playlist_tracks`, then `tracks`. `playlist_tracks.file_hash` has **no** ON DELETE
      CASCADE, so a bare delete FK-errors *(Gate 2 §4.3 — the only Gate 2 item left)*
- [ ] Home "Remove from library" — confirmation must name the corrected-segment count
- [ ] `AnalysisWorker` QThread over the `analyse` / `persist` split
- [ ] Add-songs dialog + progress dialog with Cancel ("Finishing current song…", never a freeze)
- [ ] Home wiring: Add button, right-click entry, **rename "Analyze" → "Correct labels"**
- [ ] Main-window wiring: persist on `fileDone`, `home.refresh()` on finish *(also closes H4)*
- [ ] Live verification on the rig: multi-song add · remove-and-re-add · cancel mid-separation
      then a clean re-run · **playback-during-analysis checked by ear** *(settles decision D3)*

## Gate 4 — robustness

- [ ] **H1** app refuses to start without loopMIDI → null port + persistent "MIDI disabled" banner
- [ ] **H2** only `NoSegmentsError` caught around `attach()` → catch broadly, keep previous track playing
- [ ] **H3** correction CLI skips `ensure_calibration_copy` → call it in the save path
- [ ] **H5** bare `except Exception` misreports playlist failures → catch `sqlite3.IntegrityError`

## Gate 5 — packaging

- [ ] `pyproject.toml` with **fully pinned deps** — the 2026-08-19 CI break is the evidence;
      only `librosa` and `audioop-lts` are pinned today. Drop `pyqtgraph` and `requests`
- [ ] `guitar_helper/ui/state/__init__.py` — PyInstaller misses implicit namespace packages
- [ ] App data in `%LOCALAPPDATA%\GuitarHelper`, seeded on first run; remove CWD-relative defaults
- [ ] Bundle + first-run-seed `archetypes.json` (3 KB) — without it classification silently
      falls back to uncalibrated defaults
- [ ] Bundle htdemucs_6s weights (~52 MB) and set `model_dir` explicitly — they currently land
      in `C:/tmp/audio-separator-models/`, which cleanup tools remove
- [ ] Bundle torch + onnxruntime
- [ ] **Measure the frozen build size early** — torch's PyInstaller footprint is the biggest
      unknown in the whole estimate; finding out last is the expensive way
- [ ] Clean-machine test: no Python, no venv, no MSVC. `python-rtmidi` must not compile
- [ ] README + LICENSE + setup guide: loopMIDI, **VST2 or standalone — not VST3**, ffmpeg

## Gate 6 — documentation

- [ ] User guide for the three modes + a Help entry point
- [ ] Coding conventions in the README / contributor section — they live only in `CLAUDE.md`
      today, which a human contributor has no reason to open
- [ ] Final `SAVE_STATE.md` pass

---

## Small carry-overs

- [ ] `run_calibrate.py:14` still tells the reader not to pass `--no-separate`, which no longer exists
- [ ] Promote `audio_loader._SUPPORTED_NATIVE` / `_SUPPORTED_PYDUB` to public names; drop the
      `run_batch._AUDIO_EXTENSIONS` alias
- [ ] `source_separator._check_cache` — cognitive complexity 21, refactor candidate (not a ruff failure)
- [ ] librosa 1.0.0 upgrade as a **deliberate** step with a `run_calibrate` re-run — feature output
      changes, which would shift classification with no test failing
- [ ] CI: `actions/checkout@v4` and `actions/setup-python@v5` are forced off deprecated Node 20
- [ ] `ruff.toml` targets `py312` while the runtime is 3.14
- [ ] Hygiene: 5 stray `.db` files at root, `batch_reanalysis.log`, `print_db.py`
- [ ] Delete merged branches `docs/mvp-readiness-review`, `fix/issue-006-008-wave`

## Not in MVP scope

QOL pass (`Phase_4_QOL_changes.md`) · latency measurement on the rig · review findings M1–M7 ·
`MainWindow` SRP extraction · moving `ui/editor/` to fix the Tier 1 → Tier 3 import · lyrics ·
spectrum · cover art · queue persistence · `ambient` re-add · more `overdrive` labels ·
stem-cache eviction.
