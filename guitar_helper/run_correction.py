"""CLI runner: interactively correct segments for an analysed track.

Usage:
    python -m guitar_helper.run_correction <file_hash> [--db library.db]
"""
from __future__ import annotations

import argparse
import sys

from guitar_helper.correction.cli import SegmentCorrectionTool
from guitar_helper.db.repository import SQLiteSegmentStore
from guitar_helper.db.schema import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Correct segments for a track.")
    parser.add_argument("file_hash", help="SHA-256 hash of the audio file (from run_analysis output)")
    parser.add_argument("--db", default="library.db")
    args = parser.parse_args()

    conn = init_db(args.db)
    store = SQLiteSegmentStore(conn)

    if not store.get_segments(args.file_hash):
        print(f"No segments found for hash {args.file_hash!r}. Run run_analysis first.", file=sys.stderr)
        sys.exit(1)

    SegmentCorrectionTool(store).run(args.file_hash)


if __name__ == "__main__":
    main()
