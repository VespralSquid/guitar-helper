# TODO

Working checklist. **`docs/plans/mvp-implementation-plan.md` is the authority** on
scope and ordering — this file is the scannable view, not a second source of truth.
If the two disagree, the plan wins.

_Updated 2026-08-26. **All six gates done.** Gate 5 (packaging) landed 2026-08-25;
the separation tier was bundled 2026-08-26. 634 tests, ruff clean. Remaining before
a public release: the htdemucs_6s licence check, a clean-machine test, a by-hand
pass over the installer consent page, and live rig verification._

---

## Next — live verification on the rig (settles D3)

Wave 2 shipped the whole GUI ingestion path. Nothing else is code-blocked; what
remains is the by-ear check the plan insisted on rather than inferred.

- [x] Multi-song add through **Add songs…** — real files, real separation
- [ ] Remove-and-re-add: labels reset, separation is **not** repeated (stem kept, D5)
- [ ] Cancel mid-separation, then a clean re-run — the cancelled stem must not poison the cache
- [x] **Playback during analysis, checked by ear** *(settles D3)*. The "Pause playback
      while analysing" option ships defaulted **off**; if you hear dropouts, tick it and
      the default flips to on. ISSUE-005's lesson is that code inspection is a hypothesis

## Gate 4 — robustness — **DONE**

- [x] **H1** null `NullMidiPort` fallback + persistent "MIDI disabled" banner
- [x] **H2** `attach()` failures caught broadly; the previous track keeps playing
- [x] **H3** correction CLI saves through the atomic `apply_edits`, which snapshots first
- [x] **H5** playlist creation catches `sqlite3.IntegrityError` only; other errors surface

## Gate 5 — packaging — **DONE** (2026-08-25; separation bundled 2026-08-26)

Spec + as-built record: `docs/new feature specs/Packaging_and_Update_Strategy.md` (§11).

- [x] `guitar_helper/ui/state/__init__.py` — it was the only implicit namespace package.
      The build worked without it, but it is the exact case freezers miss, so it is now explicit
- [x] App data in `%LOCALAPPDATA%\GuitarHelper`, seeded on first run; CWD default now applies
      only to a source checkout (`AppConfig.resolve` branches on `sys.frozen`)
- [x] Bundle + first-run-seed `archetypes.json` — `ensure_user_resources()` never overwrites
      an existing copy, so an update cannot discard the user's calibration
- [x] **Measured the frozen build size.** Without separation: 374 MB tree -> 106 MB
      installer. With it (current): 847 MB -> 247 MB
- [x] PyInstaller onedir spec, Inno Setup per-user installer, install→run→uninstall verified;
      uninstall leaves the data dir byte-for-byte intact
- [x] `python-rtmidi` no longer needs to compile — the bundle ships the built `.pyd`
- [x] Manifest updater: HTTPS-only, SHA-256 verified before launch, `/SILENT` handoff
- [x] Schema safety for an update channel: `SchemaTooNewError` downgrade guard + automatic
      pre-migration backup

- [x] **Separation tier bundled** (reversed 2026-08-26 — see spec §7). torch CPU + ONNX
      Runtime + audio-separator + htdemucs_6s weights. 847 MB tree -> 247 MB installer.
      The ~2 GB that justified excluding it is a CUDA install; the CPU build is 498 MB.
      Separation is the product: `build_pipeline` always uses `AudioSeparator` and
      `archetypes.json` is calibrated on stem features, so a build without it analyses nothing
- [x] `diffq` stub vendored at `installer/stubs/diffq.py` — it lived only in the dev venv,
      so a fresh checkout could not build a working app
- [x] Installer consent page listing every bundled component; Next disabled until confirmed,
      skipped under `WizardSilent()` so the updater's `/SILENT` handoff cannot hang

**Deliberately not done:**
- `pyproject.toml` — not needed. The app ships as a frozen bundle, not a wheel; the freeze
  pins the actual resolved versions. Pinning `requirements.txt` is still worth doing for
  reproducible builds, but it is no longer a packaging blocker

### Gate 5 leftovers — before a public release

- [x] **ffmpeg decision — resolved 2026-08-26: user prerequisite, not bundled.** The locally
      available build is msys2's, which is GPL and dynamically linked. `check_ffmpeg()` is now
      a BLOCK (audio-separator's `Separator.__init__` raises without it, so nothing analyses),
      and the installer page shows the winget command plus live PATH state
- [ ] **htdemucs_6s licence — now live.** The weights are redistributed as of 2026-08-26.
      Demucs upstream is MIT but the shipped checkpoint has not been verified. Close before
      any public release
- [ ] Clean-machine test: no Python, no venv, no MSVC, **no ffmpeg** (confirm the preflight blocker reads clearly rather than looking like a crash)
- [ ] Click through the installer consent page by hand — Next-disabled-until-ticked and the live ffmpeg line are the one part not verifiable without a human
- [ ] Exercise the update flow against a real GitHub release (only unit-tested with a fake transport)
- [ ] Code signing — unsigned installers hit a SmartScreen wall. Spec §2
- [ ] README + setup guide: loopMIDI, **VST2 or standalone — not VST3**, ffmpeg

## Gate 6 — documentation

- [x] User guide for the three modes (`docs/user-guide.md`) + Help menu entry point
- [x] Coding conventions in the README contributor section
- [x] `README.md` + `LICENSE` (MIT — **confirm the copyright holder**, see below)
- [x] Final `SAVE_STATE.md` pass

---

## Small carry-overs

- [x] `run_calibrate.py` docstring no longer references the removed `--no-separate`
- [ ] **Confirm the `LICENSE`**: MIT / "Aryan Kumar" / 2026 was chosen by default, not from
      evidence. Check it against the private `VespralSquid/guitar-helper` remote. The Demucs
      weight licence half of this is now urgent — the weights ARE redistributed as of
      2026-08-26 (tracked under Gate 5 leftovers)
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
