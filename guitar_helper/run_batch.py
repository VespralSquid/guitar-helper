"""Batch analysis CLI: analyse every audio file in a folder and store results.

Usage:
    python -m guitar_helper.run_batch <folder> [options]

Options mirror run_analysis.py plus:
    --recursive     Descend into subdirectories (default: top-level only)
    --reanalyze     Re-run even if segments already exist in the DB
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.analysis.pipeline import AnalysisPipeline
from guitar_helper.analysis.source_separator import AudioSeparator, NullSeparator
from guitar_helper.db.interfaces import Segment
from guitar_helper.db.repository import SQLiteSegmentStore
from guitar_helper.db.schema import init_db

_AUDIO_EXTENSIONS = {".wav", ".flac", ".ogg", ".aiff", ".mp3", ".m4a", ".aac"}


@dataclass
class _SongResult:
    path: Path
    status: str          # "ok" | "skip" | "fail"
    file_hash: str = ""
    segments: list[Segment] = field(default_factory=list)
    error: str = ""


def _discover_audio(folder: Path, recursive: bool) -> list[Path]:
    """Return sorted audio files under folder (top-level or recursive)."""
    pattern = "**/*" if recursive else "*"
    return sorted(
        p for p in folder.glob(pattern)
        if p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS
    )


def _read_tags(path: Path) -> tuple[str | None, str | None]:
    """Return (title, artist) from file tags; fall back to (stem, None)."""
    try:
        from mutagen import File as MutagenFile  # noqa: PLC0415
        audio = MutagenFile(path, easy=True)
        if audio is not None:
            title  = audio["title"][0]  if "title"  in audio else path.stem
            artist = audio["artist"][0] if "artist" in audio else None
            return title, artist
    except Exception:  # noqa: BLE001
        pass
    return path.stem, None


def _tone_summary(segments: list[Segment]) -> str:
    counts = Counter(s.tone_label for s in segments)
    return " ".join(f"{label}({n})" for label, n in sorted(counts.items()))


def _format_ms(ms: int) -> str:
    total_s, millis = divmod(ms, 1000)
    minutes, seconds = divmod(total_s, 60)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-analyse all audio files in a folder.")
    parser.add_argument("folder", help="Path to folder containing audio files")
    parser.add_argument("--db", default="library.db")
    parser.add_argument("--k", type=int, default=None, help="Force segment count (default: auto per song)")
    parser.add_argument("--recursive", action="store_true", help="Descend into subdirectories")
    parser.add_argument("--reanalyze", action="store_true", help="Re-run even if segments already in DB")
    parser.add_argument("--no-separate", action="store_true", help="Skip guitar source separation")
    parser.add_argument("--hpss", action="store_true", help="Isolate harmonic content before features")
    parser.add_argument("--verbose", action="store_true", help="Print segmenter diagnostics per song")
    parser.add_argument("--stems-dir", default="stems")
    parser.add_argument("--model-dir", default=None)
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Error: not a directory — {folder}", file=sys.stderr)
        sys.exit(1)

    paths = _discover_audio(folder, args.recursive)
    if not paths:
        print(f"No audio files found in {folder}")
        sys.exit(0)

    print(f"Found {len(paths)} audio file(s) in {folder}\n")

    loader = AudioLoader()
    conn = init_db(args.db)
    store = SQLiteSegmentStore(conn)
    separator = (
        NullSeparator()
        if args.no_separate
        else AudioSeparator(cache_dir=args.stems_dir, model_dir=args.model_dir, verbose=args.verbose)
    )
    pipeline = AnalysisPipeline(
        store, separator=separator, verbose=args.verbose, use_hpss=args.hpss
    )

    results: list[_SongResult] = []
    total = len(paths)

    for idx, path in enumerate(paths, start=1):
        prefix = f"[{idx}/{total}]"
        try:
            _, file_hash = loader.load(path)

            if not args.reanalyze and store.get_segments(file_hash):
                results.append(_SongResult(path=path, status="skip", file_hash=file_hash))
                print(f"{prefix}  SKIP    {path.name:<40}  (already in DB)")
                continue

            title, artist = _read_tags(path)
            segments = pipeline.run(path, title=title, artist=artist, k=args.k)
            results.append(_SongResult(path=path, status="ok", file_hash=file_hash, segments=segments))
            tone_str = _tone_summary(segments)
            print(f"{prefix}  OK      {path.name:<40}  {len(segments)} segs   {tone_str}")

        except Exception as exc:  # noqa: BLE001
            results.append(_SongResult(path=path, status="fail", error=str(exc)))
            print(f"{prefix}  FAILED  {path.name:<40}  {exc}")

    _print_summary(results, args.db)


def _print_summary(results: list[_SongResult], db_path: str) -> None:
    ok    = [r for r in results if r.status == "ok"]
    skip  = [r for r in results if r.status == "skip"]
    fail  = [r for r in results if r.status == "fail"]
    total_segments = sum(len(r.segments) for r in ok)

    print(f"\n=== Batch complete: {len(results)} file(s) ===")
    print(f"  Analyzed : {len(ok):<3}  ({total_segments} segments stored)")
    print(f"  Skipped  : {len(skip):<3}  {'(use --reanalyze to force)' if skip else ''}")
    print(f"  Failed   : {len(fail)}")

    if ok or skip:
        print("\nFile hashes (for python -m guitar_helper.run_correction <hash>):")
        for r in ok + skip:
            if r.file_hash:
                print(f"  {r.path.name:<42}  {r.file_hash[:16]}...")

    print(f"\nStored in {db_path}.")


if __name__ == "__main__":
    main()
