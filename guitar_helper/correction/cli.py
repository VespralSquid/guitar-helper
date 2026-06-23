from __future__ import annotations

from guitar_helper.analysis.tone_classifier import TONE_LABELS
from guitar_helper.db.interfaces import ISegmentStore, Segment


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


def _confirm(seg: Segment) -> Segment:
    """Affirm a segment's existing label as user-verified ground truth."""
    return Segment(
        id=seg.id, file_hash=seg.file_hash,
        start_ms=seg.start_ms, end_ms=seg.end_ms,
        tone_label=seg.tone_label, confidence=1.0, manually_corrected=True,
    )


def _parse_confirm(tokens: list[str], segments: list[Segment]) -> tuple[Segment, ...] | None:
    """Parse a `confirm <idx>` or `confirm all` command."""
    if len(tokens) != 2:
        return None
    if tokens[1] == "all":
        return tuple(_confirm(s) for s in segments)
    try:
        idx = int(tokens[1])
    except ValueError:
        return None
    if idx < 0 or idx >= len(segments):
        print(f"  Index {idx} out of range (0–{len(segments) - 1}).")
        return None
    return (_confirm(segments[idx]),)


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

    seg = segments[idx]

    if len(tokens) == 2:
        label = tokens[1]
        if label not in TONE_LABELS:
            print(f"  Unknown label {label!r}. Valid: {', '.join(TONE_LABELS)}")
            return None
        return (Segment(
            id=seg.id, file_hash=seg.file_hash,
            start_ms=seg.start_ms, end_ms=seg.end_ms,
            tone_label=label, confidence=1.0, manually_corrected=True,
        ),)

    if len(tokens) == 4:
        try:
            start_ms, end_ms = int(tokens[1]), int(tokens[2])
        except ValueError:
            print("  start_ms and end_ms must be integers.")
            return None
        label = tokens[3]
        if label not in TONE_LABELS:
            print(f"  Unknown label {label!r}. Valid: {', '.join(TONE_LABELS)}")
            return None
        if start_ms >= end_ms:
            print("  start_ms must be less than end_ms.")
            return None
        if idx > 0 and start_ms < segments[idx - 1].end_ms:
            print(f"  start_ms {start_ms} overlaps segment {idx - 1} (ends at {segments[idx - 1].end_ms}).")
            return None
        if idx < len(segments) - 1 and end_ms > segments[idx + 1].start_ms:
            print(f"  end_ms {end_ms} overlaps segment {idx + 1} (starts at {segments[idx + 1].start_ms}).")
            return None
        return (Segment(
            id=seg.id, file_hash=seg.file_hash,
            start_ms=start_ms, end_ms=end_ms,
            tone_label=label, confidence=1.0, manually_corrected=True,
        ),)

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
