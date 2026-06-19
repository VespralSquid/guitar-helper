# Batch / Playlist Analysis — `run_batch.py`

## Context
`run_analysis.py` processes one song at a time and requires `--title`/`--artist` per invocation. Data-driven calibration (ISSUE-003 Fix A) requires analyzing a full performance set at once, then labeling segments via the correction CLI. The batch runner removes the per-song friction and makes "upload a playlist" a single command for both calibration and live-performance preparation.

User decisions: skip already-analyzed songs by default (re-check by file hash; `--reanalyze` to override), read title/artist from file tags via `mutagen` (fallback to filename stem if tags absent).

---

## CLI Interface

```
python -m guitar_helper.run_batch <folder>
    [--db library.db]
    [--no-separate]        # skip guitar stem separation (same as run_analysis)
    [--hpss]               # isolate harmonic content (same as run_analysis)
    [--verbose]            # per-song segmenter diagnostics
    [--stems-dir stems]    # stem cache directory
    [--model-dir ...]      # separation model directory
    [--k K]                # force segment count for every song (default: auto per song)
    [--recursive]          # descend into subdirectories (default: top-level only)
    [--reanalyze]          # re-run even if segments already exist in DB
```

Supported extensions: `.wav`, `.flac`, `.ogg`, `.aiff`, `.mp3`, `.m4a`, `.aac`

---

## Core Logic

```
discover_audio(folder, recursive) → sorted list[Path]

for each path:
    try:
        duration_ms, file_hash = AudioLoader().load(path)
        if not reanalyze and store.get_segments(file_hash):
            record status=SKIPPED; print one-liner; continue
        title, artist = _read_tags(path)    # mutagen, fallback to path.stem / None
        segments = pipeline.run(path, title, artist, k)
        record status=OK, segments
        print one-liner: "  [OK]  filename  8 segments  edge(3) crunch(5)"
    except Exception as e:
        record status=FAILED, error=str(e)
        print warning; continue

print summary table
```

### Skip check
`AudioLoader().load()` is cheap (SHA-256 chunk read + soundfile.info). Calling it separately before `pipeline.run()` is fine — hashing is fast.

### Output format
Per-song progress (printed immediately on completion):
```
[1/4]  OK      Gunslinger.wav              8 segs   edge(3) crunch(5)
[2/4]  SKIP    Kansas.wav                  (already in DB)
[3/4]  FAILED  bad_file.mp3                UnicodeDecodeError: ...
[4/4]  OK      I_Hate_Everything.wav       8 segs   edge(4) crunch(4)
```
Final summary:
```
=== Batch complete: 4 files ===
  Analyzed : 2   (16 segments stored)
  Skipped  : 1   (use --reanalyze to force)
  Failed   : 1
```
Print file hashes for all analyzed songs (needed for run_correction):
```
File hashes (for run_correction):
  Gunslinger.wav        b21c43...
  I_Hate_Everything.wav 223aa5...
```

---

## Files Created / Modified

| File | Change |
|---|---|
| `guitar_helper/run_batch.py` | New — full implementation |
| `requirements.txt` | Added `mutagen` |
| `tests/test_run_batch.py` | New — unit tests |

No changes to: `pipeline.py`, `audio_loader.py`, `db/`, `source_separator.py`.
