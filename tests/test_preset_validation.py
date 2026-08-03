from __future__ import annotations

import pytest

from guitar_helper.db.interfaces import Preset
from guitar_helper.ui.editor.preset_validation import (
    apply_name,
    apply_pc,
    validate_name,
    validate_pc,
)

PRESETS = [
    Preset("clean", "Clean", 0),
    Preset("edge", "Edge of Breakup", 1),
    Preset("metal", "Metal", 4),
    Preset("other", "Other", -1),
]


def test_accepts_unused_pc():
    assert validate_pc(PRESETS, "clean", 7).ok


def test_accepts_keeping_own_pc():
    """Re-entering a row's current value is not a clash with itself."""
    assert validate_pc(PRESETS, "clean", 0).ok


def test_rejects_pc_already_mapped():
    result = validate_pc(PRESETS, "clean", 4)
    assert not result.ok
    assert "metal" in result.error


@pytest.mark.parametrize("pc", [128, 200, -2])
def test_rejects_out_of_range_pc(pc):
    assert not validate_pc(PRESETS, "clean", pc).ok


def test_other_must_stay_no_dispatch():
    """'other' holding the current preset is a project invariant, not a default."""
    result = validate_pc(PRESETS, "other", 5)
    assert not result.ok
    assert "other" in result.error


def test_only_other_may_use_no_dispatch():
    result = validate_pc(PRESETS, "clean", -1)
    assert not result.ok


def test_other_keeping_minus_one_is_valid():
    assert validate_pc(PRESETS, "other", -1).ok


@pytest.mark.parametrize("name", ["", "   "])
def test_rejects_blank_name(name):
    assert not validate_name(name).ok


def test_accepts_name():
    assert validate_name("Nolly Lead").ok


def test_apply_pc_leaves_other_fields():
    updated = apply_pc(PRESETS[0], 9)
    assert updated.pc_number == 9
    assert updated.tone_label == "clean"
    assert updated.preset_name == "Clean"


def test_apply_name_strips_whitespace():
    assert apply_name(PRESETS[0], "  Nolly Clean  ").preset_name == "Nolly Clean"
