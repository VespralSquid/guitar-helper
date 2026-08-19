# Lane B design decisions — ISSUE-008 (stem cache integrity)

**Scope:** master-plan Gate 2 §4.2 only. Files owned: `guitar_helper/analysis/source_separator.py`,
`tests/test_source_separator.py`. Nothing else is edited.
**Frozen contract honoured:** `ISourceSeparator.separate_guitar(path, file_hash) -> Path` is
unchanged — no new parameters, not even optional ones. Every fact this design needs is
derived from `path`, `file_hash`, the cache directory and the model directory.

All measurements below were taken on this machine with the project venv against the real
46 MB stem and the real 55 MB model file. They re-derive nothing from ISSUE-008; they settle
the questions ISSUE-008 left open.

---

## 0. Three measured findings that change the plan

These are why the note deviates from the issue document in three places. Each is reproducible
in under ten seconds.

**F1 — `stem_bytes` does NOT catch the zero-fill case.** ISSUE-008 test 2 says a zero-filled
same-size stem is "rejected (requires S2's `stem_bytes`, or S4)". Measured:

```
good.wav      bytes=1764044  frames=441000  dur=10.000
zerofill.wav  bytes=1764044  frames=441000  dur=10.000   <- identical size AND duration
```

The 34-byte delta in ISSUE-008's evidence (`46,493,744` vs `46,493,778`) is an artifact of how
that repro rewrote the file, not a property of zero-filling. A genuine Class-B1 power-loss
zero-fill preserves byte count exactly. **`stem_bytes` and `sf.info` together are blind to the
one failure mode the orchestrator singled out as the dangerous one.** Only content inspection
catches it. This forces S4 from Optional to Must (D5).

**F2 — `Separator.separate()` swallows every exception.** `separator.py:935` wraps
`self._separate_file(...)` in `except Exception as e: self.logger.error(...)` and returns the
accumulated (possibly empty) `output_files` list. Today `separate_guitar` discards that return
value and returns `cached` unconditionally, so a failed separation returns a path to a file
that does not exist, and the failure surfaces two layers later as a librosa error. The
implementation must check for the produced file and raise its own error.

**F3 — `os.replace` fails on Windows if the destination is open by anyone.**

```
replace over existing file      : OK
replace while dest open for read: PermissionError [WinError 5] Access is denied
```

`os.replace` is still atomic — it is never partially applied — but it is not
unconditionally-succeeding. The publish step needs a retry and a defined fallback, and that
fallback is what makes test 9 (concurrent separations) pass rather than flake. Also measured:
`os.fsync` requires a descriptor opened `O_RDWR` (fails `EBADF` on `O_RDONLY`), and directory
fsync raises `PermissionError` on Windows — the POSIX "fsync the parent dir after rename"
idiom must be skipped, not attempted.

Supporting measurements used below:

| Quantity | Measured |
|---|---|
| `sf.info` on the 46 MB stem | 0.171 ms |
| 3-window (3 MiB) sampled digest, warm | **2.75 ms** |
| full sha256 of the 46 MB stem, warm | 40.5 ms |
| full sha256 of the 55 MB model `.th` | 50 ms, and equals the filename suffix (see D2) |
| model-identity derivation from the model dir | 0.464 ms |
| `importlib.metadata.version("audio-separator")` | 17 ms, **does not import torch** |
| `shutil.disk_usage` | 0.375 ms; volume is 94.5% used, 52.0 GB free |
| stem duration vs source duration, all 9 live stems | **delta 0 ms in 9 of 9** |

---

## Decisions

### D1. Which manifest fields are obtainable, and from where

**Options:** (a) the manifest as printed in ISSUE-008 §S2 verbatim; (b) drop anything not
cheaply obtainable; (c) keep every field but split "recorded" from "enforced".

**Decision:** (c). Every field in ISSUE-008's example is obtainable, but three of them must
**not** gate a cache hit. The manifest carries two classes of field and the code treats them
differently:

| Field | Source | Enforced on hit? |
|---|---|---|
| `cache_format_version` | module constant | **yes** |
| `source_hash` | the `file_hash` argument | **yes** |
| `stem_bytes` | `os.stat` on the published file | **yes** |
| `stem_frames`, `stem_samplerate` | `sf.info` on the published file | **yes** |
| `stem_sample_digest` | 3-window sampled sha256 (D5) | **yes** |
| `source_duration_ms` | `sf.info(source)` at publish/grandfather time; `null` if unreadable | **yes, against the stem's own duration** |
| `model` | module constant `_MODEL` | **yes**, when both sides known |
| `model_files` | model-dir derivation (D2) | **yes**, when both sides known |
| `separator_version` | `importlib.metadata.version("audio-separator")` | **no — recorded only** |
| `created_at` | `datetime.now(UTC).isoformat()` | no |
| `grandfathered` | D3 | no (informational; see D3) |

**Why:** `separator_version` is obtainable in 17 ms without importing torch, so there is no
feasibility reason to drop it — but enforcing it would invalidate the entire 400 MB cache on
every `pip install -U audio-separator`, including patch bumps that cannot change output.
Class C3 is a *deliberate* invalidation and its lever is `_CACHE_FORMAT_VERSION` (S5), bumped
by a human who knows the output convention changed. Recording the version is what makes that
human decision possible; enforcing it makes the tool hostile.

**Nothing is dropped for infeasibility.** `model_file_hash` is the one field that changes
shape, and it changes because a better value exists, not because the value is unobtainable —
see D2.

**Consequence for the implementer:** write all eleven fields. Compare only the six marked
"yes", in the order given by §"Cache-hit validation sequence". Never compare
`separator_version` or `created_at`.

### D2. How model identity is derived (replaces `model_file_hash`)

**Options:** (a) sha256 the 55 MB `.th` on every hit; (b) sha256 it once and memoise per
process; (c) hash the 21-byte model config only; (d) record the referenced weight
**filenames and sizes**, no content hashing.

**Decision:** (d), stored as a list, under the key `model_files`. Derivation: read the model
config (`<model_dir>/htdemucs_6s.yaml`, 21 bytes, content `models: ['5c90dfd2']`), regex out
the hex signatures, glob `<sig>*` in the model dir, keep files whose suffix is in
`{.th,.onnx,.ckpt,.pt,.pth}`, record `[{"name": ..., "bytes": ...}]` sorted by name.
Memoised once per `AudioSeparator` instance. Measured 0.464 ms cold, free thereafter.

**Why:** demucs checkpoint filenames are content-addressed — `5c90dfd2-34c22ccb.th`, and the
sha256 of that file's bytes measured `34c22ccb381c6f9f...`. **The filename suffix is the
sha256 prefix.** A different weights build cannot keep the same filename, so filename+size is
as discriminating as the hash for every realistic Class-C1 case, at 0.5 ms instead of 50 ms,
and it degrades gracefully when the file is absent. (a) is 50 ms per hit for nothing; (b)
hides a first-hit stall and still reads 55 MB; (c) identifies only the reference, not the
weights. ISSUE-008's own example value `"5c90dfd2-34c22ccb"` was already the filename — this
decision makes that explicit and adds the size as a truncation/tamper check on the weights.

**Model-dir resolution without importing torch:** `self._model_dir` if set, else
`os.environ["AUDIO_SEPARATOR_MODEL_DIR"]`, else the literal `"/tmp/audio-separator-models/"`.
That is exactly `Separator.__init__`'s own precedence (`separator.py:112`, `:166-175`) and must
be replicated as the same literal string — including its drive-relative behaviour on Windows —
so our idea of the model dir can never disagree with the library's.

**Fail-soft rule:** if the config file is missing, or the glob finds no weight file, current
identity is `None`. A comparison where **either** side is `None` is **skipped**, not failed.
This makes Class D3 (a cleanup tool wiping `C:\tmp\`) cost nothing instead of costing nine
re-separations.

**Consequence for the implementer:** one memoised private method returning
`tuple[str, list[dict] | None]`. No yaml import — regex `[0-9a-f]{6,}` on the config text.
Never hash the `.th`.

### D3. Grandfathering — lazy, source-validated, unbounded

**Options:** (a) lazy on first hit; (b) a one-time sweep script run at implementation time;
(c) lazy but only for files older than a hard-coded cutoff.

**Decision:** (a) lazy on first hit, gated on an independent duration authority.

**Why:** (b) requires writing nine JSON files into the live `stems/` directory *during the
wave*, before the gate's checksum re-verification, and a sweep script is a new file Lane B does
not own. (c) needs an unverifiable magic date constant. (a) writes nothing into `stems/` until
the app is next actually run — which is after the gate — and it is the only option that also
repairs a manifest lost to a crash between the two publish steps.

**What it validates against:** `sf.info(source_path)`, not `tracks.duration_ms`. The separator
has no database connection and must not acquire one (`ARCHITECTURE.md` §4: the Qt main thread
is the sole SQLite owner, and separation runs off it). `path` is always a real, existing file
at this point — `AnalysisPipeline.run` calls `AudioLoader.load(path)`, which raises
`FileNotFoundError` first. Measured: all 9 live stems match their source duration to **0 ms**,
so all 9 grandfather on first hit.

**Gate — grandfather only if all four hold:**

1. `sf.info(source)` succeeds (this is the authority; no authority → no grandfathering);
2. `sf.info(stem)` succeeds;
3. `|stem_duration_ms - source_duration_ms| <= 250`;
4. the sampled digest windows, excluding the first 44 bytes, are **not** entirely zero bytes.

Check 4 is free — those bytes are already in memory from the digest read — and it is the only
thing standing between grandfathering and a pre-existing Class-B1 zero-filled stem. It is
applied **at grandfather time only**, never at publish time: at publish time an all-zero result
means separation genuinely produced silence, and rejecting it would loop forever.

**How `"grandfathered": true` affects later validation: it does not.** The flag is recorded
because provenance was assumed rather than observed, and it is read by humans and by nothing
else. In particular a grandfathered manifest is **not** exempt from the model or
`cache_format_version` checks — a later model change must invalidate these stems too, because
the assumption that the current model produced them is exactly what stops being true.

**Consequence for the implementer:** grandfathering is a branch of the miss path that can
return a hit. It writes the manifest through the same atomic publish helper as a fresh
separation. If any of the four checks fails, it is an ordinary miss and re-separation follows.

### D4. S6 — moving the dev stem cache out of OneDrive

**Decision: config-only, no code change, out of Lane B's scope. Do not do it in this wave.**

`AppConfig` already exposes `--stems-dir` and `stems=`, and `run_analysis.py:61` /
`run_batch.py:109` already pass `cache_dir=str(cfg.stems_dir)`. Moving the cache is
`--stems-dir D:\GuitarHelper\stems` plus physically moving 400 MB — a change to invocation and
to the filesystem, not to `source_separator.py`. Use `--stems-dir`, **not**
`GUITAR_HELPER_HOME`, which would drag `library.db`, `music/` and `archetypes.json` along with
it.

It must not happen during the wave: the integration gate (§6 step 6) re-checksums `stems/*.wav`
*in place*, and moving them is precisely the destructive operation the hard rule forbids.
Record it as a post-gate operational item.

One thing Lane B *does* take from taxonomy D1: `os.stat` already returns `st_file_attributes`
(measured `0x20` — ARCHIVE only, fully materialised) on the stems today. A
`FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` (`0x00400000`) bit is checked on the existing stat result
and, if set, produces a **verbose warning only**. It never rejects: reading a dehydrated file
succeeds when online, and rejecting would turn a slow read into minutes of re-separation. Note
`stat.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` does not exist in the `stat` module on this Python
(only `FILE_ATTRIBUTE_OFFLINE` and `..._SPARSE_FILE` do), so the literal is required, guarded by
`getattr(st, "st_file_attributes", 0)` so the line is a no-op off Windows.

### D5. S4 (sampled digest) — **include, promoted to Must**

**Options:** skip per the default; 2-window (first + last 1 MiB); 3-window (first + middle +
last); full-file digest.

**Decision:** 3-window sampled sha256, `_DIGEST_WINDOW = 1 << 20`, offsets
`sorted({0, (size - W) // 2, size - W})` clamped at 0 and deduped, with the file size mixed into
the hash preimage. Measured **2.75 ms** warm (2-window: 1.83 ms; full file: 40.5 ms).

**Why it is promoted:** finding F1. Without a content check, *nothing* in the Must set detects a
same-size zero-filled or garbage-filled stem — not `stem_bytes`, not `sf.info`, not
`stem_frames`. That failure produces an all-`other` track and disables MIDI dispatch for the
whole song while looking like a successful analysis. Paying 2.75 ms on a path whose alternative
is minutes of separation, to close the single most dangerous hole, is not a close call. The
third window costs 0.92 ms over two and covers mid-file corruption from a partial copy (B3).

Full-file digest is **not** adopted: 40.5 ms warm, disk-bound and far worse cold, and it
duplicates a whole-file read that `librosa.load` is about to perform anyway. It belongs behind
an explicit "Verify stem cache" maintenance action, which is out of scope.

**Consequence for the implementer:** one module-level helper used by three call sites (publish,
hit validation, grandfathering) so the value is deterministic across them. The window scheme is
pinned by `_CACHE_FORMAT_VERSION` — changing the scheme means bumping the version.

### D6. S7 (free-space precheck) — **include, minimal form**

**Decision:** one `shutil.disk_usage(cache_dir).free` call at the top of the miss path; raise
`SeparationError` if `free < max(2 * source_bytes, 200 * 1024**2)`. Measured 0.375 ms.

**Why:** S1 already prevents disk-full from *corrupting* the cache — a short temp file fails
post-write validation and never publishes. The precheck's value is different: it refuses in
0.375 ms instead of burning three minutes of CPU on a run that cannot succeed, on a volume
measured at 94.5% used. The factor of 2 covers the temp copy plus the published copy; the
200 MiB floor covers demucs' own working files.

The *batch-level* precheck ISSUE-008 §S7 describes (file count × typical stem size) stays out —
`run_batch.py` is Lane C's file.

### D7. What happens to an existing but invalid cached stem

**Options:** quarantine it (rename to `<hash>_guitar.wav.rejected-<ts>`); delete it up front;
leave it and let the publish `os.replace` over it.

**Decision:** leave it; the publish overwrites it. No quarantine.

**Why:** quarantine doubles disk consumption for a rejected 46 MB file on a 94.5%-full volume,
and the file is by definition already established as untrustworthy. Deleting up front is worse
than leaving it — if separation then fails, the user has lost a stem that might have been merely
mislabelled.

**Consequence — the operational rule for the whole lane:** the runtime *does* overwrite an
invalid `*_guitar.wav`, which means **nothing the implementer runs may point a cache directory at
the repository's `stems/`.** Every test uses `tmp_path`. No manual invocation of
`separate_guitar` against `stems/`. The nine live stems are protected by the fact that they all
grandfather (measured, 9/9 at 0 ms delta), not by the code refusing to write.

### D8. Failure surface presented to Lane C

**Decision:** one new exception class, `SeparationError(RuntimeError)`, raised for: separation
produced no usable output (F2), post-write validation rejected the produced file, insufficient
disk space (D6), and publish failed against a destination that is not already valid. One class,
distinguishing messages.

**Why:** `separate_guitar` currently raises nothing of its own, so any new exception is a
cross-lane surface change even though the signature is frozen. Keeping it to one class means
Lane C's eventual per-file error handling has exactly one thing to catch. This is additive, not
a contract change — flag it to the integrator so Lane C's `analyse()` error path knows about it.

### D9. Miss reasons are returned, not printed

**Decision:** the validation logic lives in a private method returning `str | None` — `None`
means "valid hit", a string is a stable machine-readable reason code. `separate_guitar` prints
it under `verbose`.

**Why:** the nine tests need to assert *why* a stem was rejected, and asserting on captured
stdout is brittle. This costs nothing and makes tests 1–5 precise instead of "it re-separated,
probably for the right reason".

---

## Signatures / schema

### Module constants

```python
_GUITAR_STEM = "Guitar"                      # unchanged
_MODEL = "htdemucs_6s.yaml"                  # unchanged
_CACHE_FORMAT_VERSION = 1
_DEFAULT_MODEL_DIR = "/tmp/audio-separator-models/"   # must match separator.py:112 literally
_MODEL_DIR_ENV = "AUDIO_SEPARATOR_MODEL_DIR"
_WEIGHT_SUFFIXES = frozenset({".th", ".onnx", ".ckpt", ".pt", ".pth"})
_DIGEST_WINDOW = 1 << 20
_DURATION_TOLERANCE_MS = 250
_STALE_TMP_AGE_S = 6 * 3600
_MIN_FREE_BYTES = 200 * 1024**2
_WAV_HEADER_BYTES = 44
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
```

`_DURATION_TOLERANCE_MS = 250` is justified by the 9/9 measurement of a 0 ms delta; the smallest
truncation worth worrying about (5%) is 13 s, four orders of magnitude larger.
`_STALE_TMP_AGE_S = 6 h` is far longer than any single separation (minutes), so the sweep can
never delete a live run's temp directory.

### Manifest — `<cache_dir>/<file_hash>_guitar.json`

```json
{
  "cache_format_version": 1,
  "source_hash": "09d86e9932d472c682104b98f31d3524f605eef4ef9cc0ea9d02f93d297e1a5e",
  "source_duration_ms": 263569,
  "stem_bytes": 46493778,
  "stem_frames": 11623355,
  "stem_samplerate": 44100,
  "stem_sample_digest": "sha256-3x1M:d58fc7fecb760345...",
  "model": "htdemucs_6s.yaml",
  "model_files": [{"name": "5c90dfd2-34c22ccb.th", "bytes": 54996327}],
  "separator_version": "0.44.2",
  "created_at": "2026-08-18T12:00:00+00:00",
  "grandfathered": false
}
```

Types: `cache_format_version` int · `source_hash` str · `source_duration_ms` int **or null** ·
`stem_bytes` int · `stem_frames` int · `stem_samplerate` int · `stem_sample_digest` str ·
`model` str · `model_files` list[{name: str, bytes: int}] **or null** · `separator_version` str
**or null** · `created_at` str · `grandfathered` bool.

`source_duration_ms` is null only when `sf.info(source)` fails (non-native format). It is never
null on a grandfathered manifest — grandfathering requires that authority (D3). `stem_frames`
above is illustrative; the real value is whatever `sf.info` reports.

### Private API to write

```python
class SeparationError(RuntimeError):
    """Separation did not produce a usable, publishable stem."""

def _sample_digest(path: Path, size: int) -> tuple[str, bytes]:
    """Return (digest string, concatenated sampled bytes).

    Windows at sorted({0, (size - W)//2, size - W}) clamped to >= 0, deduped, W = 1 MiB.
    Digest preimage is str(size).encode() then each window's bytes in offset order.
    Returns the raw bytes too, so callers can test them for all-zero without a second read.
    """

def _stem_duration_ms(info) -> int:      # int(info.frames / info.samplerate * 1000)

class AudioSeparator(ISourceSeparator):
    def __init__(self, cache_dir=..., model_dir=None, verbose=False) -> None: ...   # unchanged

    def separate_guitar(self, path: Path, file_hash: str) -> Path: ...              # FROZEN

    def _manifest_path(self, file_hash: str) -> Path
    def _model_identity(self) -> tuple[str, list[dict] | None]        # memoised per instance
    def _check_cache(self, cached: Path, source: Path, file_hash: str) -> str | None
    def _try_grandfather(self, cached: Path, source: Path, file_hash: str) -> str | None
    def _build_manifest(self, stem: Path, file_hash: str,
                        source_duration_ms: int | None, *, grandfathered: bool) -> dict
    def _publish_json(self, manifest: dict, dest: Path) -> None       # atomic, retrying
    def _sweep_stale_tmp(self) -> None                                # once per instance
    def _source_duration_ms(self, source: Path) -> int | None         # sf.info, None on failure
```

`_check_cache` returns `None` on a valid hit, or one of these reason codes:

```
"no-wav"            "empty-wav"        "no-manifest"       "manifest-unreadable"
"format-version"    "source-hash"      "stem-bytes"        "model"
"stem-unreadable"   "stem-shape"       "duration"          "content"
```

`_try_grandfather` returns `None` on success (manifest now written) or one of:

```
"no-source-duration"   "stem-unreadable"   "duration"   "all-zero"
```

### Cache-hit validation sequence — exact order, with branches

Ordered cheapest-and-most-discriminating first; the only file-content read is last. Every branch
labelled MISS means: log the reason under `verbose`, fall through to separation.

```
 1. cached.exists()                            -> false: MISS "no-wav"
 2. st = cached.stat(); st.st_size == 0        -> true:  MISS "empty-wav"
 2a. st.st_file_attributes & 0x400000          -> warn only (OneDrive dehydrated). Never a MISS.
 3. manifest_path.exists()                     -> false: goto GRANDFATHER
 4. json.loads(manifest_path.read_text())      -> OSError / JSONDecodeError / not-a-dict:
                                                          MISS "manifest-unreadable"
 5. m["cache_format_version"] != 1             -> MISS "format-version"
 6. m["source_hash"] != file_hash              -> MISS "source-hash"
 7. m["stem_bytes"] != st.st_size              -> MISS "stem-bytes"
 8. cur = self._model_identity()
    if cur[1] is not None and m["model_files"] is not None:
        (cur[0], cur[1]) != (m["model"], m["model_files"]) -> MISS "model"
    otherwise skip (fail-soft, D2)
 9. info = sf.info(cached)                     -> LibsndfileError: MISS "stem-unreadable"
    info.frames != m["stem_frames"]
      or info.samplerate != m["stem_samplerate"]           -> MISS "stem-shape"
10. m["source_duration_ms"] is not None and
    abs(_stem_duration_ms(info) - m["source_duration_ms"]) > 250   -> MISS "duration"
11. _sample_digest(cached, st.st_size)[0] != m["stem_sample_digest"] -> MISS "content"
12. HIT — return cached.

GRANDFATHER (reached only from step 3):
 G1. src_ms = self._source_duration_ms(source)   -> None: MISS "no-source-duration"
 G2. info = sf.info(cached)                      -> LibsndfileError: MISS "stem-unreadable"
 G3. abs(_stem_duration_ms(info) - src_ms) > 250 -> MISS "duration"
 G4. digest, sampled = _sample_digest(cached, st.st_size)
     all(b == 0 for b in sampled[44:])           -> MISS "all-zero"
 G5. _publish_json(_build_manifest(cached, file_hash, src_ms, grandfathered=True),
                   manifest_path)
 G6. HIT — return cached.
```

A missing `m` key must be treated as `"manifest-unreadable"`, not as a `KeyError` — use
`m.get(...)` with a sentinel, so a truncated or hand-edited manifest is a miss, not a crash.

Steps 5–8 are pure dict/stat comparisons; step 9 parses a header; step 11 reads 3 MiB. Total on
a valid hit: ~3.1 ms (0.06 stat + ~0.1 json + 0.17 sf.info + 2.75 digest). A hit must **not**
read the source file — steps 1–12 touch only the stem and its manifest. The source is read only
on the grandfather path and at publish time.

### Atomic publish sequence

```
P1.  free = shutil.disk_usage(self._cache_dir).free
     if free < max(2 * source.stat().st_size, _MIN_FREE_BYTES):
         raise SeparationError(...)                     # message names both numbers
P2.  self._sweep_stale_tmp()                            # once per instance
P3.  tmp = self._cache_dir / f".tmp-{file_hash[:8]}-{os.getpid()}-{uuid4().hex[:8]}"
     tmp.mkdir(parents=True)
P4.  try:
       from audio_separator.separator import Separator  # STILL LAZY, still inside the method
       sep = Separator(output_dir=str(tmp), output_format="WAV",
                       output_single_stem=_GUITAR_STEM,
                       **({"model_file_dir": self._model_dir} if self._model_dir else {}))
       sep.load_model(model_filename=_MODEL)
       produced_names = sep.separate(str(path), {_GUITAR_STEM: f"{file_hash}_guitar"})
P5.    produced = tmp / f"{file_hash}_guitar.wav"
       if not produced.exists():
           wavs = sorted(tmp.glob("*.wav"))
           if len(wavs) == 1: produced = wavs[0]
           else: raise SeparationError(f"separator produced no usable output: {produced_names}")
P6.    info = sf.info(produced)                         # LibsndfileError -> SeparationError
       src_ms = self._source_duration_ms(path)
       if src_ms is not None and abs(_stem_duration_ms(info) - src_ms) > 250:
           raise SeparationError(...)                   # truncated / disk-full output, NOT published
P7.    fd = os.open(produced, os.O_RDWR); os.fsync(fd); os.close(fd)   # O_RDWR required (F3)
       # no directory fsync: raises PermissionError on Windows (F3)
P8.    manifest = self._build_manifest(produced, file_hash, src_ms, grandfathered=False)
P9.    manifest_path.unlink(missing_ok=True)            # best-effort
P10.   _replace_with_retry(produced, cached)            # 3 attempts, 0.25 s apart, on OSError
         final failure: if self._check_cache(cached, path, file_hash) is None: return cached
                        else: raise SeparationError(...)
P11.   self._publish_json(manifest, manifest_path)      # write into tmp, fsync, os.replace
         final failure: verbose warn, continue — do NOT raise
P12. finally:
       shutil.rmtree(tmp, ignore_errors=True)
     return cached
```

P9 before P10 is deliberate: during the publish window a reader must see **no manifest** (a miss
that grandfathers, or re-separates) rather than a manifest describing the previous wav — a
*wrong* provenance claim. A crash between P9 and P11 leaves a valid stem with no manifest, which
the grandfather path repairs on the next hit. The cost of that ordering is self-healing; the cost
of the other ordering is not.

P11 failing is non-fatal because the stem on disk is valid and correct; raising would discard
minutes of completed CPU work to report a JSON write failure. The next hit grandfathers it.

`_publish_json` writes to `<tmp or cache>/.mf-<uuid>.json`, fsyncs, then `os.replace`s onto the
destination, with the same 3× retry. A half-written manifest must never be observable.

`_sweep_stale_tmp` globs `self._cache_dir / ".tmp-*"` and `shutil.rmtree(..., ignore_errors=True)`
each directory whose `st_mtime < time.time() - _STALE_TMP_AGE_S`. Guarded by a per-instance
`self._swept` flag so a batch pays for it once. It runs on the miss path, not in `__init__` — a
constructor must not do filesystem work, and `AudioSeparator` is constructed once per app session
anyway, so "first miss" and "startup" are the same moment in practice.

---

## Sequencing

The implementer works in this order. Each step leaves the file importable and the suite runnable,
so a failure localises.

1. **Constants, `SeparationError`, `_sample_digest`, `_stem_duration_ms`, `_source_duration_ms`.**
   Pure functions, no behaviour change. Unit-test `_sample_digest` determinism first — three
   later steps depend on it agreeing with itself.
2. **`_model_identity`** with the fail-soft `None` branches. Test against a fake model dir under
   `tmp_path`; never point it at `C:\tmp\audio-separator-models`.
3. **`_build_manifest` + `_publish_json`.** Now a valid cache entry can be constructed in a test
   without a separator, which every later test needs.
4. **`_check_cache`** (steps 1–12). Tests 1, 2, 3, 4, 5 land here, and none of them needs the
   separation path at all — they assert on the returned reason code.
5. **`_try_grandfather`.** Test 8.
6. **`_sweep_stale_tmp`.** Test 7. Independent of everything else.
7. **The rewritten miss path** — P1 through P12. Tests 6, 9, 10, 11. This is the only step that
   touches the lazy import, and it is last so a mistake here cannot mask a validation bug.
8. **Repair the pre-existing tests** (below), then `ruff check` on the two owned files only.

Do step 4 before step 7 specifically because the existing tests break at step 7, and you want the
new validation proven before you are also debugging the publish path.

**Three of the five existing tests in `tests/test_source_separator.py` are affected and must be
rewritten, not deleted:**

- `test_audio_separator_cache_hit_skips_separation` pre-seeds `cached.write_bytes(b"fake-wav")`
  and asserts a hit. Under this design that is a `"stem-unreadable"` miss, so the `_boom` stub
  fires and the test fails. Rewrite it to build a genuine hit — a small `soundfile`-written wav
  plus a manifest from `_build_manifest` — and keep the `_boom` stub, because "a valid hit never
  imports `audio_separator`" is the assertion worth preserving.
- `test_audio_separator_cache_key_naming` does the same with `b"x"`. Same rewrite.
- `test_source_separator_imports_without_audio_separator` still passes but is now load-bearing:
  keep it and do not weaken it.

`test_null_separator_returns_input_unchanged` and `test_audio_separator_default_cache_dir` are
unaffected. `NullSeparator` is not touched by this lane at all.

**Test fixtures build audio with `soundfile`, not with a separator.** A 2-second 44.1 kHz stereo
PCM_16 wav is ~350 kB and writes in single-digit milliseconds; nothing in the tests needs a real
46 MB stem. Fixtures are defined **locally in `tests/test_source_separator.py`** —
`tests/conftest.py` is frozen for the whole wave (orchestration §4).

---

## What this does not cover

- **Class B corruption confined to the middle of a large file, outside the three sampled
  windows.** A 46 MB stem with garbage at the 30% mark passes every check. A full-file digest
  (40.5 ms) would close it; it is deferred to an explicit "Verify stem cache" maintenance action
  that is not in this wave.
- **An externally planted, correct-duration, non-silent stem accepted by grandfathering** (B3).
  Grandfathering cannot verify provenance — that is what the flag records. Not a regression:
  today *every* such file is accepted unconditionally.
- **Non-native source formats.** `sf.info` cannot read `.m4a`/`.mp3`, so for such a source
  `source_duration_ms` is `null`, the P6 post-write duration check is skipped, and grandfathering
  refuses (`"no-source-duration"`). Harmless today — ffmpeg is absent and all 9 library files are
  WAV — but the first format ffmpeg unblocks will have a weaker post-write check than WAV does.
  The clean fix is passing `duration_ms` into `separate_guitar`; that is a frozen-contract change
  and belongs to Wave 2.
- **The `source_duration_ms` vs `tracks.duration_ms` cross-check the master plan names.** The
  separator has no DB access by design (`ARCHITECTURE.md` §4). The substituted authority is
  `sf.info(source)`. It catches strictly more than the DB value for the case that matters (a stem
  mapped to the wrong source) and strictly less for the case where the source file itself has been
  replaced — which changes `file_hash` and therefore the cache key anyway.
- **A model *file* replaced in place under the same filename and size.** Filename+size is not a
  content hash. Demucs filenames are content-addressed, so this requires deliberate tampering.
- **True mutual exclusion between concurrent separations.** There is no lock. Both runs do the
  full minutes of work; the design only guarantees the *cache* ends valid. A cross-process lock
  file is a bigger change than Wave 1 should carry, and the wasted CPU is a performance bug, not a
  correctness one.
- **A sub-millisecond window during concurrent publish** where process B has replaced the wav but
  not yet written its manifest (P10 done, P11 pending). A third reader in that window sees no
  manifest and grandfathers or re-separates. Self-healing, and strictly better than the ordering
  that would present a wrong manifest.
- **S6.** Deliberately not done here (D4); operational item for after the gate.
- **Batch-level free-space precheck, and any UI surface** ("Clear stem cache", Cancel, progress).
  Lane C and Wave 2.
- **Cache eviction.** Explicitly out of scope per master plan §10.

---

## Verification checklist

Nine tests from ISSUE-008 §Test plan, plus three the findings above force. **No test imports
`audio_separator`, constructs a real `Separator`, or runs a real separation.** The miss path is
exercised by injecting a stub into `sys.modules["audio_separator.separator"]` via
`monkeypatch.setitem`, exactly as the existing test does. Every test uses `tmp_path`; none may
reference the repository's `stems/`.

| # | Test | Assertions it must make |
|---|---|---|
| 1 | Truncated stem is a miss | Build a valid wav+manifest, then truncate the wav on disk. Assert `_check_cache(...) == "stem-bytes"` (size differs before the header is even parsed). Then a second case where the manifest is regenerated for the truncated size: assert `"duration"`. Then assert `separate_guitar` invokes the stub separator (a flag the stub sets) rather than returning the truncated file. |
| 2 | **Zero-filled same-size stem is a miss** | Build a valid wav+manifest; rewrite bytes 44..EOF as `\x00` **in place, preserving length**. Assert `os.path.getsize` is unchanged **and** `sf.info(...).frames` is unchanged — this is the point of the test, per F1 — then assert `_check_cache(...) == "content"`. A test that only asserts "miss" has not proven the digest is what caught it. |
| 3 | Missing manifest, wav not vouched for → miss | Valid-looking wav, no manifest, and a **source of a different duration**. Assert `_check_cache(...) == "duration"` (via the grandfather branch). Second case: no manifest and no readable source → `"no-source-duration"`. Assert no manifest file was created in either case. |
| 4 | Different model identity → miss | Manifest with `model_files=[{"name": "deadbeef-00000000.th", "bytes": 1}]` and a fake model dir containing a config plus a differently named weight file. Assert `"model"`. **Then the fail-soft pair:** with the model dir absent, assert the same manifest is a **hit** (`None`); with `model_files: null` in the manifest, assert a **hit**. All three assertions are required — the fail-soft rule is what protects against Class D3. |
| 5 | Different `cache_format_version` → miss | Manifest with `cache_format_version: 0`. Assert `"format-version"`, and assert it is reported **before** any `sf.info` call — monkeypatch `sf.info` to raise and confirm the reason is still `"format-version"`. |
| 6 | Kill mid-separation leaves nothing at the cache path | Stub `Separator` whose `separate()` raises. Assert `separate_guitar` raises, `cached` does **not** exist, `<hash>_guitar.json` does **not** exist, and `list(cache_dir.glob(".tmp-*")) == []` — the `finally` cleaned up. (ISSUE-008's wording "only a `.tmp-*` directory" is superseded: the temp dir is removed too.) Second case: stub `separate()` returns `[]` without raising (finding F2) — assert `SeparationError`, not a returned nonexistent path. |
| 7 | Stale `.tmp-*` dirs are swept | Create `.tmp-old` with `os.utime` set 7 h back and `.tmp-new` at now. Trigger a miss. Assert `.tmp-old` is gone and `.tmp-new` survives. |
| 8 | Grandfathering | Valid wav, no manifest, source of matching duration. Assert `separate_guitar` returns `cached`, the stub separator was **not** called, `<hash>_guitar.json` now exists, `json.load(...)["grandfathered"] is True`, `["source_duration_ms"]` equals the source duration, and `["stem_sample_digest"]` equals `_sample_digest(cached, size)[0]`. Then assert a **second** call is an ordinary manifest hit (`_check_cache(...) is None`). Also assert an all-zero same-duration stem is **not** grandfathered (`"all-zero"`). |
| 9 | Two concurrent separations both complete, cache ends valid | Two `AudioSeparator` instances over one cache dir, each with a stub that writes a full valid wav into its own temp dir. Drive both to publish; assert both return `cached`, `cached` exists exactly once, `_check_cache(...) is None` afterwards, and no `.tmp-*` remains. Additionally assert the retry path: monkeypatch `os.replace` to raise `PermissionError` on its first call and succeed after — publish must still succeed. Then the fallback: `os.replace` raising every time while the destination is already valid must return `cached` without raising. |
| 10 | Post-write validation blocks a truncated separation | Stub writes a wav of half the source duration into the temp dir. Assert `SeparationError`, `cached` does not exist, no manifest, no `.tmp-*`. This proves a disk-full (A3) or interrupted-output run cannot reach the cache path even once. |
| 11 | Manifest is written after the wav, and atomically | Monkeypatch `_publish_json` to raise. Assert `cached` **exists and is a complete valid wav** while the manifest does not exist, and that `separate_guitar` still returned `cached` without raising (P11 is non-fatal). Assert the next call grandfathers it. |
| 12 | A valid cache hit never imports the separator | Keep the existing `_boom` stub pattern. Build a genuine valid wav+manifest and assert `separate_guitar` returns it without the stub firing. This is the rewritten `test_audio_separator_cache_hit_skips_separation`. |

Additional invariants the suite must hold, asserted anywhere convenient:

- `_sample_digest` is deterministic across calls and independent of read chunking.
- `_sample_digest` on a file smaller than 1 MiB reads the whole file and does not raise.
- A manifest missing a required key is `"manifest-unreadable"`, not a `KeyError`.
- `import guitar_helper.analysis.source_separator` does not import `torch`, `onnxruntime` or
  `audio_separator` — at minimum keep the existing import test intact.
- `NullSeparator.separate_guitar` behaviour is byte-for-byte unchanged.
- `AudioSeparator()._cache_dir == Path("stems")` still holds (the existing default-dir test).
- `separate_guitar`'s signature is still exactly `(self, path: Path, file_hash: str) -> Path`.

Wall-clock budget: all twelve tests operate on wavs of a few hundred kB. If
`tests/test_source_separator.py` takes more than about a second, something is reading a real stem
or a real model, and it must be found before the gate.
