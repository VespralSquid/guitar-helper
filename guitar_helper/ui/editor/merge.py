"""Qt-free same-tone-run merge. Extends the run's first segment to cover the
whole run and marks the rest for deletion — never re-segments, never touches
boundaries outside the run.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from guitar_helper.db.interfaces import Segment


@dataclass
class MergePlan:
    keep: Segment
    delete_ids: list[int]


def find_same_tone_run(segments: list[Segment], index: int) -> tuple[int, int]:
    """Inclusive [lo, hi] bounds of the same-tone run containing index."""
    label = segments[index].tone_label
    lo = index
    while lo > 0 and segments[lo - 1].tone_label == label:
        lo -= 1
    hi = index
    while hi < len(segments) - 1 and segments[hi + 1].tone_label == label:
        hi += 1
    return lo, hi


def merge_run(segments: list[Segment], lo: int, hi: int) -> MergePlan:
    """Merge segments[lo..hi] (inclusive) into one. A single-segment run
    (lo == hi) is a no-op plan: keep is unchanged, nothing to delete."""
    keep = replace(
        segments[lo],
        end_ms=segments[hi].end_ms,
        confidence=1.0,
        manually_corrected=True,
    )
    delete_ids = [s.id for s in segments[lo + 1:hi + 1]]
    return MergePlan(keep=keep, delete_ids=delete_ids)
