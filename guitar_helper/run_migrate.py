"""CLI: migrate an existing library into the installed app's data directory.

A development library and an installed build do not share a root, and
`tracks.source_path` is absolute, so copying `library.db` across by hand leaves
every row pointing at wherever the audio used to live. That matters more than it
looks: a dead `source_path` also defeats the stem cache, because a stem with no
completion manifest is only rescued by `_try_grandfather()`, which needs the
source file to compare durations against. Migrating badly therefore costs a full
re-separation per track.

Usage:
    python -m guitar_helper.run_migrate --dry-run
    python -m guitar_helper.run_migrate
    python -m guitar_helper.run_migrate --source-db old.db --relink-into "D:\\Music"
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.config import user_data_dir
from guitar_helper.db.schema import SchemaTooNewError, init_db


@dataclass(frozen=True)
class Relink:
    file_hash: str
    filename: str
    old_path: str
    new_path: str | None
    status: str   # "kept" | "relinked" | "unresolved" | "hash-mismatch"


def _candidates(stored: Path, search_dirs: list[Path]) -> list[Path]:
    """Where this track's audio might now live, best guess first.

    The stored path is tried last, not first: when the same file exists both in
    the old location and in the app's own library folder, the app's copy is the
    one that survives the checkout being deleted.
    """
    found = [d / stored.name for d in search_dirs]
    found.append(stored)
    seen: set[str] = set()
    unique: list[Path] = []
    for path in found:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def resolve_track(
    loader: AudioLoader,
    file_hash: str,
    filename: str,
    stored: str,
    search_dirs: list[Path],
) -> Relink:
    """Pick a path whose *content* matches this track.

    The hash is re-checked before repointing. Matching on filename alone would
    happily attach a track to a different recording with the same name, and the
    stem cache is keyed by hash, so the error would surface much later as a
    silent re-separation rather than as an obvious mistake.
    """
    stored_path = Path(stored) if stored else Path(filename)
    mismatch = False

    for candidate in _candidates(stored_path, search_dirs):
        if not candidate.is_file():
            continue
        try:
            if loader.hash_file(candidate) != file_hash:
                mismatch = True
                continue
        except OSError:
            continue

        if str(candidate) == str(stored_path):
            return Relink(file_hash, filename, stored, str(candidate), "kept")
        return Relink(file_hash, filename, stored, str(candidate), "relinked")

    return Relink(
        file_hash, filename, stored, None,
        "hash-mismatch" if mismatch else "unresolved",
    )


def copy_database(source: Path, target: Path) -> Path | None:
    """Copy source over target, backing up a non-empty target first.

    Uses the sqlite backup API rather than a file copy so a WAL in flight is
    captured consistently. Returns the backup path, if one was made.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None

    if target.exists() and target.stat().st_size > 0:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = target.with_name(f"{target.name}.replaced-{stamp}")
        shutil.copyfile(target, backup)
        target.unlink()

    src = sqlite3.connect(source)
    try:
        dest = sqlite3.connect(target)
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()
    return backup


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate a library database into the installed app's data directory.",
    )
    parser.add_argument("--source-db", default="library.db",
                        help="Database to migrate from (default: ./library.db)")
    parser.add_argument("--target-db", default=None,
                        help="Destination (default: the installed app's library.db)")
    parser.add_argument("--relink-into", action="append", default=[], metavar="DIR",
                        help="Extra directory to search for relocated audio (repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing anything")
    args = parser.parse_args()

    source = Path(args.source_db).resolve()
    data_dir = user_data_dir()
    target = Path(args.target_db).resolve() if args.target_db else data_dir / "library.db"

    if not source.is_file():
        print(f"Error: no database at {source}", file=sys.stderr)
        sys.exit(2)
    if source == target:
        print(f"Error: source and target are the same file ({source})", file=sys.stderr)
        sys.exit(2)

    search_dirs = [data_dir / "music", *(Path(d) for d in args.relink_into)]

    with sqlite3.connect(source) as probe:
        tracks = _count(probe, "tracks")
        segments = _count(probe, "segments")
    print(f"Source : {source}")
    print(f"         {tracks} track(s), {segments} segment(s)")
    print(f"Target : {target}")
    print(f"Search : {', '.join(str(d) for d in search_dirs)}")
    print()

    if tracks == 0:
        print("Nothing to migrate: the source database has no tracks.")
        sys.exit(1)

    loader = AudioLoader()
    with sqlite3.connect(source) as probe:
        rows = probe.execute(
            "SELECT file_hash, filename, COALESCE(source_path, '') FROM tracks ORDER BY filename"
        ).fetchall()

    results = [resolve_track(loader, h, fn, sp, search_dirs) for h, fn, sp in rows]

    width = max(len(r.filename) for r in results)
    for r in results:
        if r.status == "relinked":
            print(f"  RELINK  {r.filename:{width}}  -> {r.new_path}")
        elif r.status == "kept":
            print(f"  keep    {r.filename:{width}}")
        elif r.status == "hash-mismatch":
            print(f"  SKIP    {r.filename:{width}}  a file of that name exists but its "
                  f"contents differ; leaving {r.old_path}")
        else:
            print(f"  MISSING {r.filename:{width}}  {r.old_path or '(no path recorded)'}")

    relinked = sum(1 for r in results if r.status == "relinked")
    unresolved = sum(1 for r in results if r.status in ("unresolved", "hash-mismatch"))
    print()
    print(f"{relinked} to relink, {len(results) - relinked - unresolved} already correct, "
          f"{unresolved} unresolved.")

    if args.dry_run:
        print("\nDry run - nothing written.")
        return

    backup = copy_database(source, target)
    if backup is not None:
        print(f"\nExisting target backed up to {backup}")

    try:
        conn = init_db(str(target))
    except SchemaTooNewError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(3)

    try:
        with conn:
            for r in results:
                if r.status == "relinked":
                    conn.execute(
                        "UPDATE tracks SET source_path = ? WHERE file_hash = ?",
                        (r.new_path, r.file_hash),
                    )
        final_tracks = _count(conn, "tracks")
        final_segments = _count(conn, "segments")
    finally:
        conn.close()

    print(f"Migrated {final_tracks} track(s), {final_segments} segment(s) to {target}")
    if unresolved:
        print(f"{unresolved} track(s) still point at missing audio; they will list but "
              f"not play until the files are restored or re-added.")

    stems = data_dir / "stems"
    have = len(list(stems.glob("*_guitar.wav"))) if stems.is_dir() else 0
    print(f"Stem cache: {have} stem(s) in {stems}")
    if have < final_tracks:
        print("  Copy your existing stems/ into that folder to avoid re-separating.")


if __name__ == "__main__":
    main()
