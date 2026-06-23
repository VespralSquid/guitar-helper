# Source Separation Tools Evaluation

_For: isolating guitar before tone analysis — Guitar Performance Assistant_
_Date: 2026-06-18_

## Problem being solved

Tone classification is inaccurate because features are extracted from the **full mix** —
vocals, drums, bass, and keys all contaminate the spectral features (flatness, ZCR, centroid,
contrast) the classifier relies on. Isolating the guitar before feature extraction should
materially improve accuracy.

## The single most important finding

**Only one model produces a dedicated guitar stem: `htdemucs_6s`** (Demucs v4, 6 stems:
vocals / drums / bass / **guitar** / piano / other). Every tool below either uses this model or
cannot isolate guitar at all. So the _model_ is effectively decided — the real choice is **which
library wraps it**.

---

## Candidate 1 — python-audio-separator (`nomadkaraoke/python-audio-separator`)

A maintained wrapper supporting MDX-Net, VR-Arch, MDXC, and Demucs models behind one API.

| Pros | Cons |
|---|---|
| Actively maintained | Pulls in **onnxruntime** as dead weight — only its Demucs path has a guitar stem, so the MDX/VR/MDXC support is irrelevant to us |
| Clean Python API; auto model **download + local caching** | Heaviest dependency footprint of the options |
| Runs fully offline after first model download | Requires ffmpeg (see cross-cutting note below) |
| Python >= 3.10 supported | Larger PyInstaller `.exe` (Phase 5) |
| CPU / GPU / Apple Silicon install extras | Extra abstraction layer over Demucs |

---

## Candidate 2 — Demucs, used directly (`facebookresearch/demucs`)

The original model. Use `htdemucs_6s` straight from the `demucs` package.

| Pros | Cons |
|---|---|
| **Leaner** — `torch` + `torchaudio` + `demucs`, no onnxruntime | **Unmaintained** — Meta archived the repo; only an unofficial fork (`adefossez/demucs`) gets occasional fixes |
| Smaller install → smaller `.exe` | You manage model download/cache yourself |
| Direct programmatic API (`get_model` / `apply_model`) | Relies on ffmpeg on Windows (torchaudio is limited there) |
| Same model quality (it _is_ the source of htdemucs_6s) | Less polished API surface |
| Well-known MIT license | |

---

## Candidate 3 — Spleeter (`deezer/spleeter`) — REJECTED

| Pros | Cons |
|---|---|
| Fast, lightweight, popular | **No guitar stem** — max 5 stems (vocals/drums/bass/piano/other) |
| | TensorFlow-based — Python 3.14 wheel support far more doubtful than torch |
| | Lower separation quality than Demucs by 2026 consensus |

**Verdict: cannot do the job** — no guitar stem.

---

## Candidate 4 — Open-Unmix (`sigsep/open-unmix-pytorch`) — REJECTED

| Pros | Cons |
|---|---|
| Lightweight, torch-based, MIT | **No guitar stem** — 4 stems only (vocals/drums/bass/other) |
| Good reference implementation | Lower quality than Demucs |

**Verdict: cannot do the job** — no guitar stem.

---

## Cross-cutting factors (apply to both viable options)

**Python 3.14 compatibility — the critical gate, and it now passes:**
- PyTorch 2.9.0 ships Python 3.14 **CPU** wheels. This project is CPU-only and offline, so the
  absence of 3.14 CUDA wheels is irrelevant.
- onnxruntime 1.27.0 ships cp314 wheels (uploaded 2026-06-15) — relevant only if we pick
  audio-separator.
- _This would have been a hard blocker a year ago; it is viable as of June 2026._

**ffmpeg:** Both require ffmpeg on Windows (torchaudio's Windows support is limited). **Not a new
blocker** — the project already needs ffmpeg for its primary `.m4a`/AAC target, and Phase 5
already plans to bundle it. Installing it now unblocks separation _and_ MP3/AAC loading at once.

**Performance:** CPU separation runs at roughly **1.5x track duration** (a 4-min song ≈ 4–6 min).
Acceptable because separation lives in the **offline analysis tier**, not the real-time
playback/MIDI path — but it makes **caching the separated stem (keyed by `file_hash`) mandatory**
so re-analysis never re-separates.

**Guitar-stem quality:** Independent testing rates htdemucs_6s guitar as "okay" — noticeably
better than its piano stem, with some bleed/artifacts. Sufficient here, since the classifier
needs _timbre/distortion character_, not pristine isolation.

**Install footprint:** torch alone is ~2 GB installed; htdemucs_6s weights ~250 MB. A meaningful
jump for a previously light project — most relevant to the Phase 5 PyInstaller bundle.

**Relationship to existing code:** there is already a lightweight `use_hpss` flag
(`librosa.effects.hpss`) on `FeatureExtractor`/`AnalysisPipeline`. Source separation occupies the
same conceptual slot ("isolate guitar-ish content") at a much higher cost/quality point — the two
are alternatives, and HPSS could remain a fast fallback.

---

## Summary recommendation

Both viable paths use **htdemucs_6s**. The trade-off is **maintenance + convenience
(audio-separator)** vs **lean footprint (demucs-direct)**. For an offline desktop app that will be
bundled with PyInstaller, the deciding question is whether to value the actively-maintained
wrapper and automatic model management (audio-separator) over a smaller, simpler dependency tree
(demucs-direct).

Two design decisions remain open before implementation:
1. **Library:** demucs-direct (lean, unmaintained) vs audio-separator (maintained, heavier).
2. **Separation scope:** guitar stem for classification only (full mix still drives
   segmentation), or guitar stem for both segmentation and classification.

## Sources

- [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator)
- [facebookresearch/demucs](https://github.com/facebookresearch/demucs)
- [PyTorch Python 3.14 support (#156856)](https://github.com/pytorch/pytorch/issues/156856),
  [CUDA wheels (#169929)](https://github.com/pytorch/pytorch/issues/169929)
- [onnxruntime on PyPI](https://pypi.org/project/onnxruntime/)
- [HTDemucs model comparison](https://stemsplitter.github.io/research/model-comparison/)
