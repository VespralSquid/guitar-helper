from __future__ import annotations

from guitar_helper.analysis.tone_classifier import TONE_LABELS
from guitar_helper.db.interfaces import ISegmentStore, Segment
from guitar_helper.ui.editor.validation import (
    apply_boundary,
    apply_confirm,
    apply_relabel,
    validate_boundary,
    validate_relabel,
)


def _format_ms(ms: int) -> str:
    total_s, millis = divmod(ms, 1000)
    minutes, seconds = divmod(total_s, 60)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def _format_segment(idx: int, seg: Segment) -> str:
    corrected = "Yes" if seg.manually_corrected else "No"
    return (
        f"[{idx}]  {_format_ms(seg.start_ms)} → {_format_ms(seg.end_ms)}"
        f"  |  {seg.tone_label:<8}"
        f"  |  conf={seg.confidence:.2f}"
        f"  |  corrected={corrected}"
    )


def _parse_confirm(tokens: list[str], segments: list[Segment]) -> tuple[Segment, ...] | None:
    """Parse a `confirm <idx>` or `confirm all` command."""
    if len(tokens) != 2:
        return None
    if tokens[1] == "all":
        return tuple(apply_confirm(s) for s in segments)
    try:
        idx = int(tokens[1])
    except ValueError:
        return None
    if idx < 0 or idx >= len(segments):
        print(f"  Index {idx} out of range (0–{len(segments) - 1}).")
        return None
    return (apply_confirm(segments[idx]),)


def _parse_relabel(tokens: list[str], seg: Segment) -> tuple[Segment, ...] | None:
    """Parse `<idx> <label>` (idx already resolved to seg)."""
    label = tokens[1]
    result = validate_relabel(label, TONE_LABELS)
    if not result.ok:
        print(f"  {result.error}")
        return None
    return (apply_relabel(seg, label),)


def _parse_boundary(tokens: list[str], segments: list[Segment], idx: int) -> tuple[Segment, ...] | None:
    """Parse `<idx> <start_ms> <end_ms> <label>` (idx already resolved)."""
    try:
        start_ms, end_ms = int(tokens[1]), int(tokens[2])
    except ValueError:
        print("  start_ms and end_ms must be integers.")
        return None
    label = tokens[3]
    relabel_result = validate_relabel(label, TONE_LABELS)
    if not relabel_result.ok:
        print(f"  {relabel_result.error}")
        return None
    boundary_result = validate_boundary(segments, idx, start_ms, end_ms)
    if not boundary_result.ok:
        print(f"  {boundary_result.error}")
        return None
    return (apply_boundary(segments[idx], start_ms, end_ms, label),)


def _parse_command(tokens: list[str], segments: list[Segment]) -> tuple[Segment, ...] | None:
    """Parse a correction command. Returns tuple of updated Segment(s) or None on error."""
    if tokens[0] == "confirm":
        return _parse_confirm(tokens, segments)

    try:
        idx = int(tokens[0])
    except (ValueError, IndexError):
        return None

    if idx < 0 or idx >= len(segments):
        print(f"  Index {idx} out of range (0–{len(segments) - 1}).")
        return None

    if len(tokens) == 2:
        return _parse_relabel(tokens, segments[idx])

    if len(tokens) == 4:
        return _parse_boundary(tokens, segments, idx)

    return None


class SegmentCorrectionTool:

    def __init__(self, store: ISegmentStore) -> None:
        self._store = store

    def run(self, file_hash: str) -> None:
        segments = self._store.get_segments(file_hash)
        if not segments:
            print(f"No segments found for hash {file_hash!r}.")
            return

        pending: dict[int, Segment] = {}

        excluded = self._store.get_calibration_excluded(file_hash)
        excl_status = "EXCLUDED from calibration" if excluded else "included in calibration"

        print("\nSegment Correction Tool")
        print(f"Track calibration status: {excl_status}")
        print(
            "Commands:  <idx> <label>  |  <idx> <start_ms> <end_ms> <label>"
            "  |  confirm <idx>  |  confirm all  |  exclude  |  include  |  s=save  q=quit\n"
        )

        while True:
            for i, seg in enumerate(segments):
                display = pending.get(i, seg)
                marker = " *" if i in pending else ""
                print(f"  {_format_segment(i, display)}{marker}")
            print()

            raw = input("» ").strip()
            if not raw:
                continue

            if raw == "q":
                print("Quit without saving.")
                return

            if raw == "exclude":
                self._store.set_calibration_excluded(file_hash, True)
                print("  Track marked as EXCLUDED from calibration.\n")
                continue

            if raw == "include":
                self._store.set_calibration_excluded(file_hash, False)
                print("  Track marked as included in calibration.\n")
                continue

            if raw == "s":
                for idx, updated in pending.items():
                    self._store.update_segment(updated)
                print(f"Saved {len(pending)} change(s).")
                return

            tokens = raw.split()
            result = _parse_command(tokens, segments)
            if result is None:
                print("  Invalid command. Try:  0 metal  |  0 1000 5000 crunch  |  confirm 0  |  confirm all  |  s  |  q\n")
                continue

            for updated in result:
                orig_idx = next(i for i, s in enumerate(segments) if s.id == updated.id)
                pending[orig_idx] = updated
                segments[orig_idx] = updated
            print()
