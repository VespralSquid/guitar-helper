"""CLI runner: analyse an audio file and print the resulting segments.

Usage:
    python -m guitar_helper.run_analysis <path> [--title TITLE] [--artist ARTIST] [--k K]

The results are stored in library.db and also printed to stdout.
Copy the file_hash printed at the top to use with the correction CLI.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from guitar_helper.analysis.pipeline import AnalysisPipeline, ManualCorrectionsExistError
from guitar_helper.analysis.source_separator import AudioSeparator, NullSeparator
from guitar_helper.analysis.tone_classifier import ThresholdClassifier
from guitar_helper.config import add_config_args, config_from_args
from guitar_helper.db.repository import SQLiteSegmentStore
from guitar_helper.db.schema import init_db


def _format_ms(ms: int) -> str:
    total_s, millis = divmod(ms, 1000)
    minutes, seconds = divmod(total_s, 60)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse an audio file and store segments.")
    parser.add_argument("path", help="Path to the audio file (WAV/FLAC/OGG)")
    parser.add_argument("--title", default=None)
    parser.add_argument("--artist", default=None)
    parser.add_argument("--k", type=int, default=None, help="Force segment count (default: auto-detect)")
    parser.add_argument("--verbose", action="store_true", help="Print segmenter diagnostics")
    parser.add_argument("--hpss", action="store_true", help="Isolate harmonic content before feature extraction")
    parser.add_argument("--no-separate", action="store_true", help="Skip guitar source separation (analyse full mix)")
    parser.add_argument("--discard-corrections", action="store_true",
                        help="Overwrite this track's manual corrections (default: refuse)")
    add_config_args(parser)
    args = parser.parse_args()
    cfg = config_from_args(args)

    path = Path(args.path)
    if not path.exists():
        print(f"Error: file not found — {path}", file=sys.stderr)
        sys.exit(1)

    print(f"Analysing: {path.name}")
    if args.k:
        print(f"  k={args.k} (forced)")
    if args.hpss:
        print("  HPSS enabled")
    if args.no_separate:
        print("  guitar separation disabled (full mix)")

    model_dir = str(cfg.model_dir) if cfg.model_dir else None
    separator = (
        NullSeparator()
        if args.no_separate
        else AudioSeparator(cache_dir=str(cfg.stems_dir), model_dir=model_dir, verbose=args.verbose)
    )

    conn = init_db(str(cfg.db_path))
    store = SQLiteSegmentStore(conn)
    classifier = ThresholdClassifier(calibration_path=cfg.archetypes_path)
    try:
        segments = AnalysisPipeline(
            store, classifier=classifier, separator=separator,
            verbose=args.verbose, use_hpss=args.hpss,
        ).run(
            path, title=args.title, artist=args.artist, k=args.k,
            discard_corrections=args.discard_corrections,
        )
    except ManualCorrectionsExistError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    file_hash = segments[0].file_hash if segments else "—"
    print(f"\nfile_hash : {file_hash}")
    print(f"segments  : {len(segments)}\n")

    for i, s in enumerate(segments):
        corrected = " [corrected]" if s.manually_corrected else ""
        print(
            f"  [{i}]  {_format_ms(s.start_ms)} -> {_format_ms(s.end_ms)}"
            f"  |  {s.tone_label:<8}"
            f"  |  conf={s.confidence:.2f}"
            f"{corrected}"
        )

    print(f"\nStored in {cfg.db_path}.")
    print(f"To correct: python -m guitar_helper.run_correction {file_hash}")


if __name__ == "__main__":
    main()
