"""Calibration tool: compute archetype vectors from manually-corrected segments.

Usage:
    python -m guitar_helper.run_calibrate [--root DIR] [--db PATH] [--stems-dir DIR]
                                           [--library-root DIR] [--archetypes PATH]

Reads all manually_corrected=1 segments from the DB, re-extracts their
extract_for_classification() feature vectors from cached stems (or raw audio
located by content hash under --library-root), computes the per-tone mean,
and writes archetypes.json.

After running, re-analyse all songs to pick up the new archetypes. Keep separation
ON (the default) so analysis features come from the same stems the archetypes were
calibrated on — do NOT pass --no-separate here:
    python -m guitar_helper.run_batch music\\ --reanalyze
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from guitar_helper.analysis.audio_loader import AudioLoader
from guitar_helper.analysis.feature_extractor import FeatureExtractor
from guitar_helper.analysis.file_locator import locate
from guitar_helper.config import add_config_args, config_from_args
from guitar_helper.db.schema import init_db

_HOP = 512


def _stems_path(stems_dir: Path, file_hash: str) -> Path:
    return stems_dir / f"{file_hash}_guitar.wav"


def _find_audio(
    file_hash: str,
    source_path: str | None,
    stems_dir: Path,
    library_root: Path | None,
    loader: AudioLoader,
) -> Path | None:
    """Return the best available audio path: stem → stored/re-hashed source."""
    stem = _stems_path(stems_dir, file_hash)
    if stem.exists():
        return stem
    return locate(file_hash, source_path, library_root, loader)


def _collect_tone_vectors(
    rows: list,
    loader: AudioLoader,
    extractor: FeatureExtractor,
    stems_dir: Path,
    library_root: Path | None,
) -> dict[str, list[np.ndarray]]:
    """Re-extract classification features for each labeled segment and group by tone."""
    tone_vectors: dict[str, list[np.ndarray]] = defaultdict(list)
    audio_cache: dict[str, tuple[np.ndarray, int]] = {}
    feature_cache: dict[str, np.ndarray] = {}
    missing: set[str] = set()

    for _, file_hash, start_ms, end_ms, tone_label, filename, source_path in rows:
        if tone_label == "other" or file_hash in missing:
            continue

        if file_hash not in audio_cache:
            audio_path = _find_audio(file_hash, source_path, stems_dir, library_root, loader)
            if audio_path is None:
                print(f"  WARNING: no audio found for {filename} ({file_hash[:12]}...) — skipping")
                missing.add(file_hash)
                continue
            y, sr = loader.load_mono(str(audio_path))
            audio_cache[file_hash] = (y, sr)

        if file_hash not in feature_cache:
            y, sr = audio_cache[file_hash]
            feature_cache[file_hash] = extractor.extract_for_classification(y, sr)

        clf_matrix = feature_cache[file_hash]
        _, sr = audio_cache[file_hash]
        n_frames = clf_matrix.shape[1]
        f0 = max(0, min(int(round(start_ms / 1000 * sr / _HOP)), n_frames - 1))
        f1 = max(f0 + 1, min(int(round(end_ms / 1000 * sr / _HOP)), n_frames))
        tone_vectors[tone_label].append(clf_matrix[:, f0:f1].mean(axis=1))

    return tone_vectors


def _build_archetypes(
    tone_vectors: dict[str, list[np.ndarray]],
    min_segments: int,
) -> dict[str, list[float]]:
    """Print coverage report and return tones that meet the minimum threshold."""
    print(f"{'Tone':<12}  {'Segments':>8}  {'Status'}")
    print("-" * 40)
    archetypes: dict[str, list[float]] = {}
    for tone, vecs in sorted(tone_vectors.items()):
        n = len(vecs)
        if n < min_segments:
            print(f"  {tone:<10}  {n:>8}  SKIPPED (< {min_segments} segments, needs more labels)")
        else:
            # Cross-segment mean. EXP-001 favored median to suppress silent-segment
            # outliers, but with those mislabels fixed (now 'other') the clean-data
            # LOOCV prefers mean (macro-F1 0.693 vs 0.660). Switch to median only if
            # future labels reintroduce outlier contamination.
            archetypes[tone] = np.mean(vecs, axis=0).astype(np.float64).tolist()
            print(f"  {tone:<10}  {n:>8}  OK")
    return archetypes


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate tone archetypes from labeled segments.")
    parser.add_argument("--min-segments", type=int, default=3,
                        help="Minimum labeled segments required per tone (default: 3)")
    add_config_args(parser)
    args = parser.parse_args()
    cfg = config_from_args(args)

    conn = init_db(str(cfg.db_path))
    rows = conn.execute("""
        SELECT s.id, s.file_hash, s.start_ms, s.end_ms, s.tone_label,
               t.filename, t.source_path
        FROM segments s
        JOIN tracks t ON s.file_hash = t.file_hash
        WHERE s.manually_corrected = 1
          AND t.calibration_excluded = 0
        ORDER BY s.tone_label, s.file_hash
    """).fetchall()

    if not rows:
        print("No manually-corrected segments found.")
        print("Use run_correction to label segments first, then re-run calibration.")
        sys.exit(0)

    print(f"Found {len(rows)} labeled segment(s) across {len({r[1] for r in rows})} track(s).\n")

    tone_vectors = _collect_tone_vectors(
        rows,
        AudioLoader(),
        FeatureExtractor(hop_length=_HOP),
        cfg.stems_dir,
        cfg.library_root,
    )

    if not tone_vectors:
        print("No usable segments after filtering. Exiting.")
        sys.exit(1)

    new_archetypes = _build_archetypes(tone_vectors, args.min_segments)

    if not new_archetypes:
        print("\nNo tones met the minimum segment threshold. Label more segments and retry.")
        sys.exit(1)

    out_path = cfg.archetypes_path
    with out_path.open("w") as f:
        json.dump(new_archetypes, f, indent=2)

    print(f"\nWrote {len(new_archetypes)} archetype(s) to {out_path}")
    print("Tones without enough labels will still use DEFAULT_ARCHETYPES as fallback.")
    print("\nNext steps:")
    print("  1. Re-analyse all songs to apply new archetypes (keep separation ON so")
    print("     analysis features match the stem-calibrated archetypes):")
    print("     python -m guitar_helper.run_batch music\\ --reanalyze")
    print("  2. Check results; label more segments if tones still mismatch.")
    print("  3. Re-run calibration to refine.")


if __name__ == "__main__":
    main()
