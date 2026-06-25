import os
import sqlite3
import tempfile

import pytest

from guitar_helper.db.schema import _CURRENT_VERSION, _DEFAULT_PRESETS, init_db


def _track_columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()}


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
    assert labels == {"clean", "edge", "overdrive", "crunch", "metal", "other"}
    assert "ambient" not in labels


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


# -----------------------------------------------------------------------
# Migrations
# -----------------------------------------------------------------------
def test_fresh_db_has_source_path(db):
    assert "source_path" in _track_columns(db)


def _make_legacy_db(path: str, version: int) -> None:
    """Build a pre-migration tracks table at the given schema version."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE presets (tone_label TEXT PRIMARY KEY, preset_name TEXT NOT NULL, pc_number INTEGER NOT NULL);
        CREATE TABLE tracks (
            file_hash TEXT PRIMARY KEY, filename TEXT NOT NULL, title TEXT, artist TEXT,
            duration_ms INTEGER NOT NULL, analysed_at TEXT NOT NULL
        );
        """
    )
    if version >= 2:
        conn.execute("ALTER TABLE tracks ADD COLUMN calibration_excluded INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at) VALUES (?,?,?,?)",
        ("legacy", "old.wav", 1000, "2020-01-01T00:00:00+00:00"),
    )
    conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (?, ?)", (version, "x"))
    conn.commit()
    conn.close()


def _make_v3_db(path: str, *, with_ambient_segment: bool = False) -> None:
    """Build a v3 DB whose presets still include 'ambient'."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE presets (tone_label TEXT PRIMARY KEY, preset_name TEXT NOT NULL, pc_number INTEGER NOT NULL);
        CREATE TABLE tracks (
            file_hash TEXT PRIMARY KEY, filename TEXT NOT NULL, title TEXT, artist TEXT,
            duration_ms INTEGER NOT NULL, analysed_at TEXT NOT NULL,
            calibration_excluded INTEGER NOT NULL DEFAULT 0, source_path TEXT
        );
        CREATE TABLE segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, file_hash TEXT NOT NULL,
            start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, tone_label TEXT NOT NULL,
            confidence REAL NOT NULL, manually_corrected INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO presets VALUES ('ambient', 'Ambient', 3), ('clean', 'Clean', 0);
        """
    )
    if with_ambient_segment:
        conn.execute(
            "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at) VALUES (?,?,?,?)",
            ("h", "x.wav", 1000, "x"),
        )
        conn.execute(
            "INSERT INTO segments(file_hash, start_ms, end_ms, tone_label, confidence) "
            "VALUES ('h', 0, 1000, 'ambient', 1.0)",
        )
    conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (3, 'x')")
    conn.commit()
    conn.close()


def _has_ambient_preset(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM presets WHERE tone_label='ambient'").fetchone() is not None


def test_v4_removes_ambient_preset():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(path)
        conn = init_db(path)
        assert not _has_ambient_preset(conn)
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == _CURRENT_VERSION
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


def test_v4_keeps_ambient_if_still_referenced():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(path, with_ambient_segment=True)
        conn = init_db(path)
        # FK must stay valid: don't delete a preset a segment still points at.
        assert _has_ambient_preset(conn)
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


def test_v6_adds_overdrive_preset():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(path)  # old DB with no overdrive preset
        conn = init_db(path)
        row = conn.execute(
            "SELECT pc_number FROM presets WHERE tone_label = 'overdrive'"
        ).fetchone()
        assert row is not None and row[0] == 4
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


@pytest.mark.parametrize("start_version", [1, 2])
def test_migration_upgrades_to_current(start_version):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        _make_legacy_db(path, start_version)
        conn = init_db(path)
        cols = _track_columns(conn)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        conn.close()
        assert version == _CURRENT_VERSION
        assert {"calibration_excluded", "source_path"}.issubset(cols)
        assert rows == 1  # existing data preserved
    finally:
        os.unlink(path)
