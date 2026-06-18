import sqlite3
import tempfile
import os

import pytest

from guitar_helper.db.schema import init_db, _DEFAULT_PRESETS, _CURRENT_VERSION


@pytest.fixture
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] for r in rows}


# -----------------------------------------------------------------------
# Test 1
# -----------------------------------------------------------------------
def test_all_tables_created(db):
    tables = _table_names(db)
    assert {"schema_version", "presets", "tracks", "segments"}.issubset(tables)


# -----------------------------------------------------------------------
# Test 2
# -----------------------------------------------------------------------
def test_foreign_keys_enabled(db):
    row = db.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


# -----------------------------------------------------------------------
# Test 3
# -----------------------------------------------------------------------
def test_default_presets_seeded(db):
    rows = db.execute("SELECT tone_label FROM presets").fetchall()
    labels = {r[0] for r in rows}
    assert len(rows) == len(_DEFAULT_PRESETS)
    assert labels == {"clean", "crunch", "metal", "ambient", "other"}


# -----------------------------------------------------------------------
# Test 4
# -----------------------------------------------------------------------
def test_other_preset_pc_minus_one(db):
    row = db.execute(
        "SELECT pc_number FROM presets WHERE tone_label = 'other'"
    ).fetchone()
    assert row is not None
    assert row[0] == -1


# -----------------------------------------------------------------------
# Test 5
# -----------------------------------------------------------------------
def test_schema_version_row(db):
    rows = db.execute("SELECT version FROM schema_version").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == _CURRENT_VERSION


# -----------------------------------------------------------------------
# Test 6
# -----------------------------------------------------------------------
def test_init_db_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        conn1 = init_db(path)
        conn1.close()
        conn2 = init_db(path)
        preset_count = conn2.execute("SELECT COUNT(*) FROM presets").fetchone()[0]
        version_count = conn2.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        conn2.close()
        assert preset_count == len(_DEFAULT_PRESETS)
        assert version_count == 1
    finally:
        os.unlink(path)


# -----------------------------------------------------------------------
# Test 7
# -----------------------------------------------------------------------
def test_segment_index_exists(db):
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_seg_lookup'"
    ).fetchone()
    assert row is not None
