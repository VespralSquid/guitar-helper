# Packaging & Update Strategy

Target: a distributable Windows desktop build of Guitar Helper, plus a long-term
update channel that does not compromise the extension points the app was designed
around.

Status: **steps 1–5 implemented (2026-08-25).** Steps 6–8 remain. See §11 for the
as-built record, including where reality differed from this plan.

---

## 1. Constraints

### What we are shipping

- Python 3.14 + PySide6 + the scientific stack (librosa / numba / llvmlite / scipy /
  sklearn). Realistic frozen bundle: **350–500 MB** after aggressive Qt exclusion.
- Two native audio libs (`libsndfile` via soundfile, `portaudio` via sounddevice).
- One compiled extension with no cp314 wheel (`python-rtmidi`).
- The separation tier (torch CPU + onnxruntime + audio-separator + htdemucs_6s
  weights). **Bundled** — see §7 for why the original "optional download"
  decision was reversed. The often-quoted ~2 GB is a CUDA torch install; the
  CPU build is 498 MB.
- One external binary dependency (ffmpeg). Required by pydub for MP3/AAC and
  **hard-required by audio-separator** — its `Separator.__init__` raises
  without it. Shipped as a documented prerequisite, not bundled (§7).

### What a frozen app cannot do

- **No `pip install`.** The interpreter is sealed. Users cannot add dependencies,
  so any extension mechanism is limited to stdlib + what we bundled.
- **No writes to the install directory.** `C:\Program Files\...` is read-only for
  standard users.
- **No overwriting a running `.exe`.** Windows holds the image lock. Every update
  mechanism works around this, not through it.
- **CWD is meaningless.** It is wherever the shortcut resolved to.

---

## 2. Tooling decisions

| Concern | Decision | Rationale |
|---|---|---|
| Freezer | **PyInstaller, onedir** | Most mature hook ecosystem for numpy/librosa/Qt. onedir starts fast, patches file-by-file, and keeps Qt DLLs separate for LGPL compliance. onefile self-extracts ~400 MB on every launch — unacceptable. |
| Installer | **Inno Setup**, per-user install to `%LOCALAPPDATA%\Programs\GuitarHelper` | Per-user install needs no UAC elevation and sidesteps the Program Files write problem entirely. |
| Update transport | **Version manifest (JSON) on GitHub Releases** | Repo is already there. No server to run. |
| Update mechanism | **Full-installer replace, silent** | ~150 lines. Deltas only if bundle size proves painful in practice — see §6. |
| Signing | **Deferred** | OV certs now require hardware-token key storage (~$200–400/yr) and still need download reputation before SmartScreen relents. Ship unsigned initially with a documented click-through. |

Rejected: **onefile** (startup cost), **MSIX/winget** (signing mandatory, rigid
layout), **Nuitka** (numba/llvmlite interaction is a research project),
**cx_Freeze** (weaker hooks for this stack).

Deferred but tracked: **tufup** (Python-native, TUF-signed, delta-capable — the
natural upgrade path if §6 triggers) and **Velopack** (best-in-class deltas, but
first-class bindings are C#/Rust/JS; Python usage means driving its CLI).

---

## 3. Step 1 — Frozen-aware path resolution

**This is the prerequisite for everything else and the only step that changes
shipping code. It is independently valuable and testable without any packaging
tooling installed.**

### Problem

`AppConfig.resolve()` (`guitar_helper/config.py:42`) resolves the base as:

```
explicit argument > GUITAR_HELPER_HOME > Path.cwd()
```

Correct for a dev checkout, wrong for an installed app on both counts: CWD is
arbitrary, and the install dir is not writable. Already flagged in
`docs/Report/mvp-readiness-review.md:208`.

### Design

Introduce a **read-only vs. writable** split. These are different lifetimes and
must not share a root.

**Writable, per-user, survives updates** — `%LOCALAPPDATA%\GuitarHelper\`:

- `library.db` and its backups
- `stems/` cache
- `music/` (or a user-chosen library root)
- user-edited `archetypes.json`
- logs, `plugins/`, optional downloaded components

**Read-only, ships in the bundle, replaced on every update** — resolved via
`sys._MEIPASS` when `sys.frozen` is set, else the repo root:

- default `archetypes.json`
- default preset seed data
- any bundled model assets

### Changes

1. Add `_default_base()` to `config.py`. When `getattr(sys, "frozen", False)`, return
   the per-user data dir; otherwise `Path.cwd()` as today. Insert it as the last
   fallback so the existing precedence chain is untouched — explicit argument and
   `GUITAR_HELPER_HOME` still win, which keeps every `run_*.py` CLI flag and every
   test using `AppConfig.resolve(tmp_path)` working unchanged.
2. Add a module-level `resource_path(name: str) -> Path` for bundled read-only
   assets. Distinct from `AppConfig` — resources are not user data and must not be
   reachable via `--root`.
3. First-run seeding: if the writable dir has no `archetypes.json`, copy the bundled
   default into it. Never overwrite an existing one. Same `user_modified` philosophy
   already applied to presets in the v9→v10 migration.
4. Directory creation. `AppConfig.resolve` currently only computes paths; the frozen
   path needs the data dir to exist. Create on first access, not at import.

### Acceptance

- `test_config.py` passes unmodified.
- New tests: frozen-mode base resolution (monkeypatch `sys.frozen`), explicit-root
  override still beats frozen default, `resource_path` resolves in both modes,
  first-run archetype copy is non-destructive.

---

## 4. Step 2 — PyInstaller spike

Goal: `GuitarHelper.exe` launches the UI and plays a track. Nothing more.

Expected time sinks, in order:

1. **librosa / numba hidden imports.** Lazy and dynamic imports (`sklearn`, `soxr`,
   numba's JIT cache dir) that static analysis misses. This is where the day goes.
   Symptom is always `ModuleNotFoundError` at runtime, never at build.
2. **PySide6 exclusion.** Default collection pulls WebEngine, 3D, Charts, Quick, and
   all translations. Explicitly exclude down to Core/Gui/Widgets — this is the
   single biggest size lever, worth several hundred MB.
3. **Native DLLs.** `libsndfile.dll`, `portaudio.dll`, the compiled `_rtmidi.pyd`.
   Usually collected automatically; verify each explicitly.
4. **numba cache location.** Must point at the writable data dir, not the bundle.

Deliverable: a checked-in `.spec` file, plus a `build.ps1` that produces the onedir
tree. Add nothing to CI yet — CI stays on ruff + pytest.

**Note:** freezing is a net *win* for `python-rtmidi`. Users currently need MSVC to
build it from source on cp314 (`requirements-runtime.txt`). Frozen, they get our
compiled `.pyd` and live MIDI works out of the box.

### ffmpeg decision — RESOLVED 2026-08-26

**Not bundled; shipped as a documented prerequisite.** The full reasoning, the
GPL problem with the locally available build, and the consequences for
`check_ffmpeg()` severity are in §7.

---

## 5. Step 3 — Installer

Inno Setup script producing `GuitarHelper-Setup-x.y.z.exe`:

- Per-user install, no elevation.
- Start Menu shortcut with the install dir as working directory (belt and braces;
  step 1 already makes CWD irrelevant).
- Uninstaller removes the install dir only. **Never touch `%LOCALAPPDATA%\GuitarHelper`** —
  the user's library, DB, and stem cache survive uninstall. Offer removal as an
  explicit, unchecked-by-default option.
- Version metadata baked into the exe so the updater can compare against the
  manifest.

---

## 6. Step 4 — The updater

### Flow

1. On launch (async, non-blocking, failure is silent), fetch
   `https://github.com/VespralSquid/guitar-helper/releases/latest/download/manifest.json`:

   ```json
   {
     "version": "1.0.1",
     "url": "https://.../GuitarHelper-Setup-1.0.1.exe",
     "sha256": "…",
     "min_upgradable_from": "1.0.0",
     "notes": "…"
   }
   ```

2. Compare against the baked-in version. If newer, prompt.
3. Download to temp. **Verify SHA-256 before executing anything.** This is the
   security boundary — an unverified update channel is remote code execution for
   anyone who can MITM it. Non-negotiable even while unsigned.
4. Launch the installer with `/SILENT /CLOSEAPPLICATIONS` and exit the app. The
   installer, not us, handles the running-exe problem.

### When to add deltas

Full replace means re-downloading ~400 MB for a one-line fix. Acceptable at low
release cadence; painful otherwise. Trigger for migrating to tufup or Velopack:
**more than roughly one release a month, or user complaints about download size.**
Not before — delta infrastructure is a real cost and premature here.

---

## 7. The separation tier — bundled (REVERSED 2026-08-26)

**This section originally said the separation stack would not be frozen and would
ship as an optional download. That decision was wrong and has been reversed: the
stack is now bundled in the base installer.**

### Why it was reversed

Separation is not an optional enhancement, it is the product. `build_pipeline`
always constructs `AudioSeparator` — "NullSeparator is test-only and must never
reach this path" — and `archetypes.json` is calibrated on *guitar-stem* features,
so classifying a full mix with it is meaningless (stem vs full-mix accuracy .821
vs .463; macro-F1 ~0.69 vs worse). A base installer without separation is not a
reduced app, it is an app that cannot analyse a single song.

The original reasoning optimised for download size against a ~2 GB estimate. That
estimate was wrong: the ~2 GB figure is a CUDA torch install. The CPU build is
498 MB, and after excluding torch's training/compile/distributed subtrees the
whole tier costs far less than assumed.

### What is bundled

| Component | Size |
|---|---|
| PyTorch, CPU build | ~498 MB (before exclusions) |
| ONNX Runtime | ~42 MB |
| onnx | ~35 MB |
| audio-separator | ~3 MB |
| htdemucs_6s weights (`5c90dfd2-34c22ccb.th`) | 55 MB |

Excluded from torch as never-reached: `torch.distributed`, `torch.testing`,
`torch._inductor`, `torch._dynamo`, `torch.include`, tensorboard, benchmark,
torchaudio, torchvision. `collect_data_files("torch")` is filtered to
json/yaml/version because unfiltered it adds ~200 MB of C++ headers.

### The two things that make this build reproducible

1. **The `diffq` stub is vendored** at `installer/stubs/diffq.py` and
   `installer/stubs` is on the spec's `pathex`. Previously it existed only as a
   hand-dropped file in the dev venv, which meant a fresh checkout could not
   produce a working build. audio-separator's bundled Demucs imports diffq
   eagerly at module load even though htdemucs_6s never calls it.
2. **The model weights are resolved at build time and their absence is fatal.**
   The spec raises `SystemExit` if they are not found, rather than producing a
   bundle that imports cleanly and then cannot separate. Location comes from
   `GUITAR_HELPER_MODEL_DIR`, defaulting to `C:	mpudio-separator-models`.
   They are not in git (55 MB).

### Model directory at runtime

`default_model_dir()` in `analysis/source_separator.py` is the single resolution
point, shared with `environment.check_model_weights()` so the preflight check and
the separator can never disagree. Frozen builds read `resource_path("models")`;
a checkout keeps the old `/tmp/audio-separator-models` default. The install is
per-user, so the bundled directory is writable and audio-separator's
makedirs/refetch paths still work if a file ever goes missing.

### ffmpeg — a prerequisite, not a bundled component

audio-separator's `Separator.__init__` calls `check_ffmpeg_installed()`, which
raises `FileNotFoundError` when ffmpeg is absent. The separator therefore cannot
even be *constructed* without it, so **no** file can be analysed — including
`.wav`.

The decision (2026-08-26) is to require it rather than bundle it. The build
available on the dev machine is msys2's, which is `--enable-gpl --enable-libx264`
and dynamically linked: bundling it would drag in its msys2 DLLs and encumber the
whole distribution with the GPL. Instead:

- The installer's components page lists ffmpeg as a separate prerequisite, shows
  the `winget install --id Gyan.FFmpeg -e` command, and reports live whether it
  was found on PATH.
- `check_ffmpeg()` is a **BLOCK**, reversing the earlier "ffmpeg is a WARN that
  becomes a per-file rejection" rule. That rule was written when a run could
  avoid separation; it cannot any more. The per-file `needs_ffmpeg` rejection
  still applies on top, to say which files additionally need ffmpeg to decode.

Revisit by shipping a static LGPL ffmpeg if the prerequisite proves to be a
support burden.

### The installer consent page

A wizard page after Welcome lists every bundled component by group
(application / analysis / separation), the disk cost, where program files and
user data go, and the ffmpeg prerequisite. **Next stays disabled until the user
ticks a confirmation box.** It is skipped under `WizardSilent()` — the updater
installs silently and consent was already given at first install.

---

## 8. Schema migration under an update channel

An installed app means **the user's DB is older than the code on every update**. The
machinery already exists — `_apply_migrations` walks every pending step in order, and
`_migrate_v9_to_v10` reconciles defaults while preserving `user_modified = 1` rows.
Two gaps to close before shipping:

1. **Automatic pre-migration backup.** Copy `library.db` to
   `library.db.bak-<from>-to-<to>-<timestamp>` before `_apply_migrations` runs, when
   and only when there is a pending step. Currently done by hand — the `.bak-*` files
   in the repo root are evidence. Retain the last N; do not grow unbounded.
2. **Downgrade guard.** `_apply_migrations` (`guitar_helper/db/schema.py:126`) uses
   `range(current + 1, _CURRENT_VERSION + 1)`, which is *empty* when
   `current > _CURRENT_VERSION`. An older build therefore opens a newer DB silently
   and operates on a schema it does not understand. Users do roll back. Raise a
   typed error and surface a clear message instead of proceeding.

---

## 9. Open/Closed under packaging

Packaging genuinely constrains extensibility, because a frozen app has no
`pip install`. Extension has to be designed in three explicit tiers, and everything
outside them is closed by construction. Deciding this **now** is the point — once a
plugin exists in the wild, the surface is a public API whether or not we meant it to be.

### Tier 1 — Data-driven (free, works perfectly)

`archetypes.json`, the `presets` table, classifier thresholds. Ship defaults as
read-only bundle resources, copy to the writable dir on first run, let users edit
their copy. Updates refresh the defaults without clobbering user edits — the
`user_modified` pattern from the v9→v10 migration generalises here.

**Most extensibility should live in this tier.** It survives freezing untouched.

### Tier 2 — Pure-Python plugins (works, with a contract)

Scan `%LOCALAPPDATA%\GuitarHelper\plugins\` and load via `importlib`. The seams
already exist as ABCs:

| Interface | Location | Extension |
|---|---|---|
| `BaseToneClassifier` | `analysis/tone_classifier.py` | Alternative classifiers |
| `IMidiPort` | `midi/` | Non-mido MIDI backends |
| `ISegmentStore` | `db/interfaces.py` | Alternative persistence |

Rules:

- Declare `PLUGIN_API = 1` in the host. Plugins declare the version they target;
  refuse to load a mismatch rather than failing obscurely later.
- Plugins may import stdlib plus the bundled dependency set **only**. Document that
  set explicitly — it is part of the contract, and silently dropping a dependency in
  a future build is a breaking change.
- Load failures are logged and skipped, never fatal.
- Plugins are user-supplied code running in-process with full privileges. Say so
  plainly in the docs; there is no sandbox and pretending otherwise is worse than
  the honest warning.

### Tier 3 — Heavy optional components (separate download)

Per §7. Anything with native dependencies or a nontrivial install is a component, not
a plugin.

---

## 10. Sequence

| # | Step | Blocking? | Notes |
|---|---|---|---|
| 1 | Frozen-aware paths (§3) | Yes — everything else depends on it | Pure code change, testable today |
| 2 | PyInstaller spike (§4) | Yes | Budget for librosa hidden imports; resolve the ffmpeg decision here |
| 3 | Inno Setup installer (§5) | Yes | |
| 4 | Schema backup + downgrade guard (§8) | Yes — before any public release | Small, independent of 2–3; can land alongside 1 |
| 5 | Manifest updater (§6) | Yes | |
| 6 | Separation component downloader (§7) | No | Base release can ship with the feature disabled |
| 7 | Plugin loader + API contract (§9 tier 2) | No | Post-1.0 |
| 8 | Code signing (§2) | No | Cost/reputation decision, revisit when there are users |

Steps 1 and 4 are worth landing before further feature work. Steps 2–5 are the
release gate. Steps 6–8 are post-1.0.

---

## 11. As built (2026-08-25)

Steps 1–5 are implemented. Numbers are from a real build on the dev machine
(Windows 11, Python 3.14.3, PyInstaller 6.22.2, Inno Setup 6.7.3).

### Artefacts

| Path | What |
|---|---|
| `installer/launcher.py` | Frozen entry point. Stream redirection, crash dialog, `--selftest`. |
| `installer/runtime_hook.py` | Redirects `NUMBA_CACHE_DIR` before librosa imports. |
| `installer/GuitarHelper.spec` | PyInstaller onedir spec. |
| `installer/GuitarHelper.iss` | Inno Setup script, per-user install. |
| `installer/build.ps1` | Freeze to `dist/GuitarHelper`. |
| `installer/release.ps1` | Freeze → installer → `manifest.json`, digest computed from the artefact. |
| `guitar_helper/update/` | Qt-free update core (manifest, verified download, installer handoff). |
| `guitar_helper/ui/update_worker.py` | `QThread` wrappers. |
| `guitar_helper/ui/dialogs/update_prompt.py` | Offer → download → install dialog. |

### Measured (2026-08-26, separation bundled)

- Frozen tree: **847 MB**.
- Installer: **247 MB** compressed, lzma2/max. torch_cpu.dll compresses well.
- Installed footprint: **852 MB**.
- Full clean build ~15 min (torch dominates the analysis phase); installer
  compile ~6 min; silent install ~25 s.

What the 847 MB actually is — there is little left to trim safely:

| File | Size |
|---|---|
| `torch/lib/torch_cpu.dll` | 293.5 MB |
| `llvmlite/binding/llvmlite.dll` | 101.7 MB (numba; predates separation) |
| `models/5c90dfd2-34c22ccb.th` | 52.4 MB |
| `GuitarHelper.exe` (embedded PYZ) | 49.4 MB |
| `PySide6/opengl32sw.dll` | 19.7 MB |
| numpy + scipy OpenBLAS (two distinct builds) | 19.5 + 19.3 MB |

No duplicated binaries between PyInstaller's own `hook-torch` and the spec's
`collect_dynamic_libs("torch")`.

### Pre-separation baseline, for reference

- Frozen tree 374 MB, installer 106 MB, build ~4.5 min + 2 min compile.

### Verified end to end (2026-08-26, separation bundled)

Install → run → uninstall exercised silently, and the interactive wizard
launched without a script runtime error (`InitializeWizard` builds the consent
page, so a fault there shows at startup):

- `--selftest` passes **21/21**, including `check_separation_stack(deep=True)` —
  a real `import audio_separator.separator`, not a `find_spec` probe.
- Model dir resolves to `<install>\_internal\models` and the weights are found.
- Silent install takes ~25 s and does **not** hang: `ShouldSkipPage` skips the
  consent page under `WizardSilent()`, which is what keeps the updater's
  `/SILENT` handoff working.
- Uninstall removes the program dir and leaves the data dir untouched (6 entries
  before and after, `library.db` intact).

**Not verified:** clicking through the consent page itself — that the Next button
stays disabled until the box is ticked, and that the live ffmpeg line renders
correctly. That needs a human at the wizard.

### Earlier pre-separation run

Install → run → uninstall was exercised silently:

- `--selftest` passes 15/15 imports **and** runs a real `FeatureExtractor.extract`,
  which is the check that matters — importing librosa proves nothing about numba,
  which JIT-compiles on first call.
- Data directory is created at `%LOCALAPPDATA%\GuitarHelper` with `library.db`,
  a seeded `archetypes.json`, `music/`, `stems/`, `cache/numba/`.
- Bundled resources resolve to the install directory via `sys._MEIPASS`.
- **Uninstall leaves the data directory byte-for-byte intact** (6 entries before
  and after), and the silent uninstall path defaults to keeping it.

### Where reality differed from the plan

1. **`installer/`, not `packaging/`.** A top-level `packaging` directory shadows
   the PyPI package of that name on `sys.path`, including as a namespace package
   with no `__init__.py`.
2. **`--selftest` was added and is not optional.** A frozen build that launches
   proves almost nothing: librosa, numba and sklearn are not touched until the
   first analysis, so a missing hidden import stays invisible until a user tries
   to add a song. Run it against every build.
3. **A frozen windowed build has `sys.stdout is None`.** Anything that prints —
   audio-separator's tqdm and INFO logging both do — raises `AttributeError` on
   `None.write`. `launcher.py` redirects both streams to
   `%LOCALAPPDATA%\GuitarHelper\guitar-helper.log` before importing anything.
4. **`Help > User guide` was broken in a frozen build.** It located the guide via
   `Path(__file__).parents[2]`, which points into `_internal` in a bundle, and
   `docs/` was not bundled at all. Now uses `resource_path()` and the guide ships
   as a data file.
5. **`.ps1` files must be pure ASCII.** Windows PowerShell 5.1 reads scripts as
   ANSI unless they carry a BOM, so an em dash in a comment is a parse error.
6. **Do not pipe PyInstaller through `2>&1` in PowerShell 5.1.** It logs to
   stderr; redirecting wraps each line in an `ErrorRecord` and reports failure on
   a successful build.
7. **winget installs Inno Setup per-user**, to
   `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`, not Program Files.
8. **`torch.distributed` and `torch.testing` cannot be excluded.**
   `torch/__init__.py` imports both unconditionally, so excluding them to save
   space breaks `import torch` outright — and takes `audio_separator.separator`
   with it. The app still launches and still shows its UI; it fails on the first
   song with a `ModuleNotFoundError` naming a module nobody would connect to
   separation. Caught by `--selftest`, which is precisely the failure mode it
   exists for. `torch._inductor`, `torch._dynamo`, `torch.utils.tensorboard`,
   `torch.utils.benchmark` and `torch.include` *are* safe — verified by
   inspecting `sys.modules` after a bare `import torch`, which is the way to
   settle this rather than by guessing.
9. **An Inno `[Code]` line may not begin with `#`.** ISPP reads any line whose
   first non-space character is `#` as a preprocessor directive, so a bare
   `#13#10 +` string continuation is "Unknown preprocessor directive". Prefix
   it (`'' + #13#10 +`).
10. **`ls _internal/` is not a way to check what is bundled.** Pure-Python packages are
   stored in the PYZ archive inside the exe, so `requests` and `mutagen` are absent
   from `_internal/` while being fully importable; only packages carrying binaries or
   data files (`certifi`, `charset_normalizer`, `soundfile`) show up as directories.
   Verifying a bundle by directory listing produces confident false negatives —
   `--selftest` is the check.

### Still open

- **ffmpeg is not bundled** (§4 decision deferred). MP3/AAC decoding through
  pydub therefore depends on ffmpeg being on the user's PATH. This must be closed
  before a public release — either bundle an LGPL build or move to
  libsndfile-native decoding.
- **Unsigned.** SmartScreen will warn on first run.
- The update flow has not been exercised against a real GitHub release; it is
  covered by unit tests with a faked transport (`tests/test_update.py`).
