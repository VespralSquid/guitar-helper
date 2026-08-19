# TODO

Working checklist. **`docs/plans/mvp-implementation-plan.md` is the authority** on
scope and ordering — this file is the scannable view, not a second source of truth.
If the two disagree, the plan wins.

_Updated 2026-08-19. Gates 1–3 done; Gate 4 done; Gate 6 done except the final
SAVE_STATE pass. 549 tests, ruff clean. **Gate 5 (packaging) is the only gate left**,
plus live rig verification._

---

## Next — live verification on the rig (settles D3)

Wave 2 shipped the whole GUI ingestion path. Nothing else is code-blocked; what
remains is the by-ear check the plan insisted on rather than inferred.

- [ ] Multi-song add through **Add songs…** — real files, real separation
- [ ] Remove-and-re-add: labels reset, separation is **not** repeated (stem kept, D5)
- [ ] Cancel mid-separation, then a clean re-run — the cancelled stem must not poison the cache
- [ ] **Playback during analysis, checked by ear** *(settles D3)*. The "Pause playback
      while analysing" option ships defaulted **off**; if you hear dropouts, tick it and
      the default flips to on. ISSUE-005's lesson is that code inspection is a hypothesis

## Gate 4 — robustness — **DONE**

- [x] **H1** null `NullMidiPort` fallback + persistent "MIDI disabled" banner
- [x] **H2** `attach()` failures caught broadly; the previous track keeps playing
- [x] **H3** correction CLI saves through the atomic `apply_edits`, which snapshots first
- [x] **H5** playlist creation catches `sqlite3.IntegrityError` only; other errors surface

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

- [x] User guide for the three modes (`docs/user-guide.md`) + Help menu entry point
- [x] Coding conventions in the README contributor section
- [x] `README.md` + `LICENSE` (MIT — **confirm the copyright holder**, see below)
- [x] Final `SAVE_STATE.md` pass

---

## Small carry-overs

- [x] `run_calibrate.py` docstring no longer references the removed `--no-separate`
- [ ] **Confirm the `LICENSE`**: MIT / "Aryan Kumar" / 2026 was chosen by default, not from
      evidence. Check it against the private `VespralSquid/guitar-helper` remote, and against
      the Demucs weight licence before those weights are redistributed in Gate 5
- [ ] `_read_tags` is duplicated between `ui/analysis_worker.py` and `run_batch.py`
- [ ] Output-mode MIDI port picker — with the banner in place, users will expect to fix a
      missing loopMIDI without relaunching; port selection is still startup-only
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
