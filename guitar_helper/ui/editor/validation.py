"""Qt-free edit validation and mutation, shared by the CLI (correction/cli.py)
and the GUI editor (EditorState). Validation and mutation are separate calls
so a caller can show a live error without committing anything.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from guitar_helper.db.interfaces import Segment


@dataclass
class EditResult:
    ok: bool
    error: str | None = None  # human-readable constraint message for live feedback


def validate_relabel(label: str, tone_labels: Sequence[str]) -> EditResult:
    if label not in tone_labels:
        return EditResult(False, f"Unknown label {label!r}. Valid: {', '.join(tone_labels)}")
    return EditResult(True)


def validate_boundary(segments: list[Segment], index: int, start_ms: int, end_ms: int) -> EditResult:
    if start_ms >= end_ms:
        return EditResult(False, "start_ms must be less than end_ms.")
    if index > 0 and start_ms < segments[index - 1].end_ms:
        return EditResult(
            False,
            f"start_ms {start_ms} overlaps segment {index - 1} "
            f"(ends at {segments[index - 1].end_ms}).",
        )
    if index < len(segments) - 1 and end_ms > segments[index + 1].start_ms:
        return EditResult(
            False,
            f"end_ms {end_ms} overlaps segment {index + 1} "
            f"(starts at {segments[index + 1].start_ms}).",
        )
    return EditResult(True)


def validate_boundary_move(segments: list[Segment], left_index: int, new_ms: int) -> EditResult:
    """Validate moving the shared edge between segments[left_index] and
    segments[left_index + 1] to new_ms — the pair stays contiguous, so this
    only needs to check that neither side inverts."""
    left = segments[left_index]
    right = segments[left_index + 1]
    if new_ms <= left.start_ms:
        return EditResult(
            False, f"Boundary must stay after segment {left_index}'s start ({left.start_ms})."
        )
    if new_ms >= right.end_ms:
        return EditResult(
            False,
            f"Boundary must stay before segment {left_index + 1}'s end ({right.end_ms}).",
        )
    return EditResult(True)


def apply_boundary_move(left: Segment, right: Segment, new_ms: int) -> tuple[Segment, Segment]:
    return (
        replace(left, end_ms=new_ms, confidence=1.0, manually_corrected=True),
        replace(right, start_ms=new_ms, confidence=1.0, manually_corrected=True),
    )


def apply_relabel(seg: Segment, label: str) -> Segment:
    return replace(seg, tone_label=label, confidence=1.0, manually_corrected=True)


def apply_boundary(seg: Segment, start_ms: int, end_ms: int, label: str) -> Segment:
    return replace(seg, start_ms=start_ms, end_ms=end_ms, tone_label=label,
                    confidence=1.0, manually_corrected=True)


def apply_confirm(seg: Segment) -> Segment:
    return replace(seg, confidence=1.0, manually_corrected=True)
