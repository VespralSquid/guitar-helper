# ISSUE-008 — An incomplete or invalid cached stem is permanently accepted as valid

**Status:** RESOLVED (2026-08-18). Fixed in commit `eb05ffd`.
**Date:** 2026-08-04
**Component:** `guitar_helper/analysis/source_separator.py` — `AudioSeparator.separate_guitar`
**Related:** ISSUE-007 §3 (cancellation), `docs/plans/gui-analysis-pipeline-plan.md` §2.3, §0.2

---

## Summary

Stem-cache validity is decided by file existence alone:

```python
# source_separator.py:43-44
cached = self._cache_dir / f"{file_hash}_guitar.wav"
if cached.exists():
    return cached
```

Any event that leaves a file at that path — complete or not, correct or not —
produces a permanent cache hit. Because the guitar stem is the *only* input to
analysis (per the mandatory-separation invariant), a bad stem silently corrupts
every segment and label derived from it, and re-analysis cannot repair it
because the bad stem is still a hit.

---

## Evidence

**Truncation.** A real stem truncated to 33%:

```
simulated interrupted separation: 46,493,778 -> 15,497,926 bytes
separate_guitar() returned the truncated file as a CACHE HIT: True
load_mono() SUCCEEDED silently -> 87.9s of audio (expected ~264s)
```

Downstream, `find_boundaries` receives the *authoritative* `duration_ms` (264 s)
while the feature matrix covers 88 s, so every detected boundary lands in the
first third and the final segment is stretched across the remaining ~176 s as one
mislabelled block.

**Same-length corruption.** A stem of the correct length whose samples are zeros:

```
zero-filled stem: bytes=46,493,744 vs good=46,493,778
  duration check: 263.57s vs expected 263.57s -> PASSES (undetected)
  what analysis would produce: every segment -> 'other' (conf 0.00)
```

The silence gate fires on every frame, so the whole track becomes `other` —
which by design means **no MIDI dispatch at all**. The amp holds one preset for
the entire song. That is precisely the failure the product exists to prevent,
arriving silently and looking like a plausible result.

---

## Failure taxonomy — every way a bad stem can reach the cache

Two cases were identified up front (process termination, user cancel). The full
set is wider, and importantly not all of it is truncation.

### Class A — incomplete write (file is short)

| | Case | Notes |
|---|---|---|
| A1 | Process killed or crashed | Window closed, Task Manager, unhandled exception on another thread, OOM |
| A2 | **User cancel** | Does not exist yet; ISSUE-007 adds it, which is why this issue blocks that work |
| A3 | **Disk full mid-write** | Stems are ~46 MB each; a 100-song batch needs ~4.6 GB plus demucs temporaries. This volume is at **95% (54 GB free)** |
| A4 | Exception inside `separator.separate()` after output began | Corrupt model file, torch OOM, an audio edge case. `separate_guitar` has no `try`/`finally`, so the partial file survives the exception |
| A5 | Sleep/hibernate or a forced update reboot mid-separation | Separation runs for minutes — a wide window |

### Class B — complete-looking file, wrong contents

| | Case | Notes |
|---|---|---|
| B1 | **Power loss / BSOD after size allocation but before data flush** | Nothing calls `fsync`. Can yield a correct-size, zero-filled or garbage file. **Verified undetectable by duration check** |
| B2 | **Concurrent separation of the same hash** | Two processes (CLI batch + GUI, or two app instances) write the same output path simultaneously. Nothing locks today |
| B3 | External tampering | Partial file copy, restore from an incomplete backup, sync conflict copy |

### Class C — valid file, wrong provenance (staleness)

| | Case | Notes |
|---|---|---|
| C1 | **Model version change** | A stem produced by a different htdemucs build. Size and duration are perfect; the *features* differ. Since `archetypes.json` is calibrated per-model, this silently mismatches classifier and data |
| C2 | Separation parameter change | `output_single_stem`, sample rate, `normalization_threshold` — same problem |
| C3 | `audio-separator` upgrade changing output conventions | Same |

Class C is invisible to **any** integrity check on the file itself. It can only
be caught by recording *what produced* the stem.

### Class D — environment

| | Case | Notes |
|---|---|---|
| D1 | **OneDrive Files On-Demand dehydration** | The stem cache is currently **inside OneDrive** (`C:\Users\aryan\OneDrive\...\stems\`). A dehydrated file still returns `True` from `exists()` and the correct `st_size`, but reading it triggers a network fetch that blocks or fails offline. Files are fully materialised (`attrib` = `A`) today, but the 95%-full volume makes dehydration more likely. 400 MB of regenerable cache is also being synced pointlessly |
| D2 | Antivirus quarantine or lock during write | |
| D3 | Cache directory cleaned by an external tool | Relevant to the model dir (`C:/tmp/...`) as well |

---

## Why the severity is high

- **Silent.** No exception at any layer. `exists()`, `sf.info()` and `librosa.load()` all accept a truncated file happily.
- **Permanent.** Re-analysis reuses the poisoned stem. With no `delete_track` and no force-reseparate (see plan §0.2), a user has no recovery path short of deleting files by hand.
- **Self-reinforcing.** The corrupted result *looks* like a real analysis — a plausible segment list — so the user is more likely to hand-correct it than to suspect the cache.
- **It will become reachable.** Today only a developer running CLI batches is exposed. ISSUE-007 adds a Cancel button, which turns A2 from theoretical into a first-class user action.

---

## Analysis of the proposed fix — "check the cached stem's length matches the song"

**The instinct is sound, and one thing about it needed verifying:** whether a
WAV header would report its *declared* length rather than the truncated reality,
which would defeat the check. It does not. libsndfile reports frames derived
from actual file size:

```
INTACT    bytes=46,493,778   sf.info dur=263.57s   actual decode=263.57s
TRUNC 75% bytes=34,870,333   sf.info dur=197.68s   actual decode=197.68s
TRUNC 33% bytes=15,342,946   sf.info dur= 86.98s   actual decode= 86.98s
TRUNC  5% bytes= 2,324,688   sf.info dur= 13.18s   actual decode= 13.18s
```

So the check **works for Class A**, costs 0.139 ms, and needs no schema change.
It is a correct and cheap first line.

**What it does not cover:** Class B (verified — the zero-filled stem passes),
Class C (a stem from another model is exactly the right length), and Class D1
(a dehydrated placeholder reports the right size and duration).

So duration matching is necessary but not sufficient. The gap is that it asks
*"is this file the right shape?"* when the question that actually matters is
*"was this file completely written, by the expected model, from the expected
source?"*

---

## Proposed solution — layered, cheapest first

Measured costs per cache hit (46 MB stem, warm):

| Check | Cost | Catches |
|---|---|---|
| `os.stat().st_size` | **0.062 ms** | Class A |
| `sf.info()` header | 0.139 ms | Class A |
| sampled digest (first + last 1 MB) | 3.2 ms | Class A, most of B |
| full 46 MB digest | 71 ms | Class A, all of B |

### S1 — Atomic publish (primary; prevents rather than detects)

`audio_separator` writes the output itself, so direct it at a per-run temp
directory and move the finished file into the cache:

```python
tmp_dir = self._cache_dir / f".tmp-{file_hash}-{os.getpid()}"
# ... separator writes into tmp_dir ...
os.replace(tmp_dir / produced_name, cached)   # atomic within the same volume
```

`os.replace` is atomic on Windows and POSIX, so a killed process can never leave
a partial file *at the cache path*. This eliminates **all of Class A** at the
source, and makes **B2 harmless** — two concurrent separations each publish a
complete file and last-writer-wins is still valid. `fsync` the file before the
replace to narrow B1.

Requires the temp dir to be on the same volume as the cache (it is, being a
subdirectory), and a startup sweep of stale `.tmp-*` directories.

### S2 — Completion manifest (the general fix)

Write `<hash>_guitar.json` **after** the wav is in place. Its presence is the
completion marker; its contents carry provenance:

```json
{
  "cache_format_version": 1,
  "source_hash": "09d86e...",
  "source_duration_ms": 263571,
  "stem_bytes": 46493778,
  "stem_frames": 11623425,
  "stem_samplerate": 44100,
  "model": "htdemucs_6s.yaml",
  "model_file_hash": "5c90dfd2-34c22ccb",
  "separator_version": "0.44.2",
  "created_at": "2026-08-04T12:00:00+00:00"
}
```

A cache hit then requires: manifest exists, parses, `cache_format_version`
matches, `source_hash` matches, model/version match, and `stem_bytes` equals
`os.stat().st_size`.

This is the classic done-file pattern. It covers Class A (no manifest → not
done), Class B3, Class C (provenance mismatch → re-separate), and gives Class D1
a hook (a dehydrated stem can be detected by attribute check before reading).

### S3 — Validation on hit

`os.stat().st_size` vs `stem_bytes` — 0.062 ms, a single syscall, no file
parsing. Strictly cheaper than the duration check and it catches the same class.
Keep the `sf.info` duration cross-check as a second assertion against
`tracks.duration_ms`, which additionally catches a stem mapped to the wrong
source.

**Honest note:** the efficiency difference between 0.062 ms and 0.139 ms is
irrelevant in a pipeline where separation takes minutes. The manifest's real
argument is **coverage**, not speed — it answers the provenance question that no
amount of file inspection can.

### S4 — Content digest (optional)

The sampled digest (first + last 1 MB, 3.2 ms) catches the zero-fill case
cheaply, because a truncated or zero-filled tail will not match. Full-file
digest (71 ms) is complete but only worth it behind an explicit
"Verify stem cache" maintenance action.

### S5 — Cache format version

A module constant bumped whenever the model, its parameters, or the output
convention changes. Recorded in the manifest; a mismatch invalidates the stem.
This is the only defence against Class C.

### S6 — Move the cache out of OneDrive

The `%LOCALAPPDATA%\GuitarHelper` packaging decision already fixes this for
shipped installs. **The dev environment should move now** — set
`GUITAR_HELPER_HOME` or `--stems-dir` outside OneDrive, which also stops 400 MB
of regenerable cache from consuming sync bandwidth and quota.

### S7 — Free-space precheck

Before a batch, refuse early if free space is below roughly (file count ×
typical stem size) plus headroom. Turns A3 from corruption into a clear message.

---

## Recommendation

| Priority | Item |
|---|---|
| **Must** | S1 atomic publish · S2 manifest · S3 size + duration check on hit |
| **Should** | S5 cache version · S6 move out of OneDrive |
| **Optional** | S4 sampled digest · S7 free-space precheck |

S1 and S2 are complementary rather than redundant: S1 prevents the bad state
from existing, S2 detects bad state that arrived by a route S1 cannot see
(Class B3, C, D).

---

## Migration of the nine existing cached stems

They have no manifest, so a strict implementation would re-separate all of them
— minutes each, for stems that are almost certainly fine.

**Grandfather them instead:** one-time, validate each stem's duration against
its `tracks.duration_ms` and, if it matches, synthesise a manifest recording the
*current* model as the assumed producer. Record in the manifest that it was
grandfathered (`"grandfathered": true`), because provenance cannot be verified
retroactively — the assumption is that the current model produced them, which is
true here but is an assumption, not a check.

---

## Test plan

All nine were implemented and pass; `tests/test_source_separator.py` carries 32 tests in
total, the remainder covering digest determinism, small-file digests, unparseable manifests,
the frozen `separate_guitar` signature and the `NullSeparator` docstring.

1. Truncated stem is rejected as a miss and re-separated (not returned as a hit).
2. Zero-filled same-size stem is rejected (requires S2's `stem_bytes`, or S4).
3. Missing manifest → miss, even when the wav looks perfect.
4. Manifest with a different `model_file_hash` → miss (Class C).
5. Manifest with a different `cache_format_version` → miss.
6. Killed mid-separation (simulate: raise inside the separator call) leaves **no**
   file at the cache path, only a `.tmp-*` directory.
7. Stale `.tmp-*` directories are swept at startup.
8. Grandfathering: an existing manifest-less stem of correct duration is accepted
   once and gains a manifest.
9. Two concurrent separations of the same hash both complete and the cache ends
   valid.

Test 2 is the one that changed the design — see below. A tenth test was added at the
integration gate: a locked or unreadable cached wav is a cache miss rather than a raw
`OSError` escaping `separate_guitar`.

---

## Current status

**All three layers implemented:** S1 atomic publish via `os.replace` from per-run temp directories; S2 completion manifest written after the WAV, carrying cache format version, source hash, model identity; S3 validation on hit requiring manifest presence, version match, hash match, and byte-size check.

Sampled digest (originally optional as a check for Class B corruption) was promoted to mandatory in the implementation. Measurement showed the byte-size check alone cannot catch zero-fill — a correct-length, zero-filled stem passes duration validation and makes every segment `other` (disabling MIDI dispatch), so the complete proof of valid contents became essential rather than optional.

Nine existing stems without manifests were grandfathered: validated against `tracks.duration_ms`, marked `"grandfathered": true` in synthesised manifests. Temp directories swept at startup; `SeparationError` raised where failed separation previously returned a nonexistent path.

---

## Lessons

- **Existence is not validity.** Any cache keyed on "the file is there" inherits
  every way a file can be there and be wrong. The completion marker has to be a
  separate artifact written last, or the cache cannot distinguish "done" from
  "started".
- **A derived artifact needs provenance, not just integrity.** Duration and
  checksums answer "is this file intact"; they cannot answer "did the thing that
  made it match the thing that will consume it". With archetypes calibrated
  per-model, that second question is the one that matters.
- **The proposed check was right about the dominant case and wrong about the
  dangerous one.** Truncation is common and loud once you look for it; the
  same-length corruption is rarer and produces an all-`other` track that
  disables MIDI dispatch entirely while looking like a successful analysis.
