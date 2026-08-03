"""Qt-free validation for preset (tone -> Program Change) edits, O4.

The presets table is the single source of truth for tone->PC, so a bad edit
here silently breaks every future dispatch. These rules keep the table in a
state the dispatcher can act on: PCs inside the MIDI range, one PC per tone,
and 'other' permanently reserved as the no-dispatch hold.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from guitar_helper.db.interfaces import Preset
from guitar_helper.ui.editor.validation import EditResult

NO_DISPATCH = -1
PC_MIN = 0
PC_MAX = 127
HOLD_TONE = "other"


def validate_pc(presets: Sequence[Preset], tone_label: str, pc_number: int) -> EditResult:
    if tone_label == HOLD_TONE and pc_number != NO_DISPATCH:
        return EditResult(
            False, f"{HOLD_TONE!r} must stay at {NO_DISPATCH} — it holds the current preset."
        )
    if tone_label != HOLD_TONE and pc_number == NO_DISPATCH:
        return EditResult(
            False, f"Only {HOLD_TONE!r} may use {NO_DISPATCH}; other tones must dispatch a PC."
        )
    if pc_number != NO_DISPATCH and not (PC_MIN <= pc_number <= PC_MAX):
        return EditResult(False, f"Program Change must be {PC_MIN}-{PC_MAX} (got {pc_number}).")
    clash = next(
        (p for p in presets if p.tone_label != tone_label and p.pc_number == pc_number), None
    )
    if clash is not None and pc_number != NO_DISPATCH:
        return EditResult(False, f"PC {pc_number} is already mapped to {clash.tone_label!r}.")
    return EditResult(True)


def validate_name(name: str) -> EditResult:
    if not name.strip():
        return EditResult(False, "Preset name cannot be empty.")
    return EditResult(True)


def apply_pc(preset: Preset, pc_number: int) -> Preset:
    return replace(preset, pc_number=pc_number)


def apply_name(preset: Preset, name: str) -> Preset:
    return replace(preset, preset_name=name.strip())
