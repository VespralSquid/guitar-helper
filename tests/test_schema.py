import os
import sqlite3
import tempfile

import pytest

from guitar_helper.db.schema import (
    _BACKUP_RETENTION,
    _CURRENT_VERSION,
    _DEFAULT_PRESETS,
    SchemaTooNewError,
    _apply_migrations,
    _migrate_v9_to_v10,
    default_preset_map,
    init_db,
    reconcile_presets,
    utcnow,
)


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


def _make_v3_db(
    path: str,
    *,
    with_ambient_segment: bool = False,
    version: int = 3,
    presets: list[tuple[str, str, int]] | None = None,
) -> None:
    """Build a DB in the tracks/segments/presets shape used from v3 onward, at
    a caller-chosen schema_version and preset population. Parametrising
    version + presets (rather than hardcoding v3 + ambient/clean) lets this
    one helper stand in for each historical layout ISSUE-006 identifies."""
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
        """
    )
    conn.executemany(
        "INSERT INTO presets(tone_label, preset_name, pc_number) VALUES (?,?,?)",
        presets if presets is not None else [("ambient", "Ambient", 3), ("clean", "Clean", 0)],
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
    conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (?, ?)", (version, "x"))
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
    # 'overdrive' is created at v6 (pc=4 at the time), then the v10 reconcile
    # (ISSUE-006) converges it onto _DEFAULT_PRESETS' current pc — 2, not the
    # historical v6 value. This test now asserts the post-reconcile position.
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(path)  # old DB with no overdrive preset
        conn = init_db(path)
        row = conn.execute(
            "SELECT pc_number FROM presets WHERE tone_label = 'overdrive'"
        ).fetchone()
        assert row is not None and row[0] == 2
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


def test_fresh_db_has_calibration_and_playlist_tables(db):
    tables = _table_names(db)
    assert {"segments_calibration", "playlists", "playlist_tracks"}.issubset(tables)


def test_migration_creates_calibration_and_playlist_tables():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(path)
        conn = init_db(path)
        tables = _table_names(conn)
        assert {"segments_calibration", "playlists", "playlist_tracks"}.issubset(tables)
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == _CURRENT_VERSION
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


def test_fresh_db_has_settings_table(db):
    assert "settings" in _table_names(db)


def test_migration_creates_settings_table():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(path)
        conn = init_db(path)
        assert "settings" in _table_names(conn)
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == _CURRENT_VERSION
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


# =========================================================================
# ISSUE-006 — v10 preset reconcile migration
# =========================================================================

def _make_v9_db_with_presets(path: str, presets: list[tuple[str, str, int, int]]) -> None:
    """A v9-shaped DB whose presets table already carries user_modified —
    simulates a hand-edited or partially-migrated DB, and exercises the
    'the column already exists' branch of _add_column_if_missing."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE presets (
            tone_label    TEXT PRIMARY KEY,
            preset_name   TEXT NOT NULL,
            pc_number     INTEGER NOT NULL,
            user_modified INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.executemany(
        "INSERT INTO presets(tone_label, preset_name, pc_number, user_modified) VALUES (?,?,?,?)",
        presets,
    )
    conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (9, 'x')")
    conn.commit()
    conn.close()


def _presets_restricted_to_defaults(conn: sqlite3.Connection) -> list[tuple]:
    default_tones = {tone for tone, _name, _pc in _DEFAULT_PRESETS}
    rows = conn.execute(
        "SELECT tone_label, preset_name, pc_number, user_modified FROM presets "
        "ORDER BY pc_number"
    ).fetchall()
    return [r for r in rows if r[0] in default_tones]


_EXPECTED_CONVERGED = [
    ("other", "Other", -1, 0),
    ("clean", "Clean", 0, 0),
    ("edge", "Edge of Breakup", 1, 0),
    ("overdrive", "Overdrive", 2, 0),
    ("crunch", "Crunch", 3, 0),
    ("metal", "Metal", 4, 0),
]

_LEGACY_LAYOUTS = [
    pytest.param(
        1,
        [
            ("other", "Other", -1), ("clean", "Clean", 0), ("crunch", "Crunch", 1),
            ("metal", "Metal", 3), ("ambient", "Ambient", 4),
        ],
        id="82542df-v1",
    ),
    pytest.param(
        1,
        [
            ("other", "Other", -1), ("clean", "Clean", 0), ("crunch", "Crunch", 1),
            ("metal", "Metal", 2), ("ambient", "Ambient", 3),
        ],
        id="d4a732e-v1",
    ),
    pytest.param(
        1,
        [
            ("other", "Other", -1), ("clean", "Clean", 0), ("crunch", "Crunch", 1),
            ("metal", "Metal", 2), ("ambient", "Ambient", 3),
            ("edge", "Edge of Breakup", 4),
        ],
        id="3ee7d86-v1",
    ),
    pytest.param(
        6,
        [
            ("other", "Other", -1), ("clean", "Clean", 0), ("crunch", "Crunch", 1),
            ("metal", "Metal", 2), ("edge", "Edge of Breakup", 3),
            ("overdrive", "Overdrive", 4),
        ],
        id="0922b7c-v6",
    ),
]


# Test 1 — convergence: every historical layout converges onto the same table.
@pytest.mark.parametrize("version,presets", _LEGACY_LAYOUTS)
def test_v10_converges_every_legacy_layout(version, presets):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(path, version=version, presets=presets)
        conn = init_db(path)
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 10
        assert _presets_restricted_to_defaults(conn) == _EXPECTED_CONVERGED
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


# Test 2 — a pre-Phase-2 DB can store an 'edge' segment after migration.
def test_v10_makes_edge_storable():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(
            path,
            version=1,
            presets=[
                ("other", "Other", -1), ("clean", "Clean", 0), ("crunch", "Crunch", 1),
                ("metal", "Metal", 3), ("ambient", "Ambient", 4),
            ],
        )
        conn = init_db(path)
        conn.execute(
            "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at) "
            "VALUES ('t', 't.wav', 1000, 'x')"
        )
        conn.execute(
            "INSERT INTO segments(file_hash, start_ms, end_ms, tone_label, confidence) "
            "VALUES ('t', 0, 1000, 'edge', 1.0)"
        )
        conn.commit()
        row = conn.execute(
            "SELECT tone_label FROM segments WHERE file_hash = 't'"
        ).fetchone()
        assert row == ("edge",)
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


# Test 3 — uniqueness survives a v1 DB with an 'ambient' segment.
def test_v10_uniqueness_with_surviving_ambient():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(
            path,
            version=1,
            presets=[
                ("other", "Other", -1), ("clean", "Clean", 0), ("crunch", "Crunch", 1),
                ("metal", "Metal", 2), ("ambient", "Ambient", 3),
            ],
            with_ambient_segment=True,
        )
        conn = init_db(path)

        dupes = conn.execute(
            "SELECT pc_number FROM presets WHERE pc_number >= 0 "
            "GROUP BY pc_number HAVING COUNT(*) > 1"
        ).fetchall()
        assert dupes == []

        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_presets_pc'"
        ).fetchone()
        assert idx is not None

        ambient = conn.execute(
            "SELECT pc_number FROM presets WHERE tone_label = 'ambient'"
        ).fetchone()
        assert ambient is not None and ambient[0] == -1

        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO presets(tone_label, preset_name, pc_number) "
                "VALUES ('dup_test', 'Dup', 0)"
            )
        conn.execute(
            "INSERT INTO presets(tone_label, preset_name, pc_number) "
            "VALUES ('dup_neg', 'DupNeg', -1)"
        )  # must not raise — -1 is exempt from the partial index
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


# Test 4 — intent preserved: a user_modified row survives the reconcile untouched.
def test_v10_preserves_user_modified_row():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v9_db_with_presets(
            path,
            [
                ("other", "Other", -1, 0),
                ("clean", "Clean", 0, 0),
                ("edge", "Edge of Breakup", 1, 0),
                ("overdrive", "Overdrive", 2, 0),
                ("crunch", "Crunch", 9, 1),
                ("metal", "Metal", 4, 0),
            ],
        )
        conn = init_db(path)
        rows = {
            r[0]: r
            for r in conn.execute(
                "SELECT tone_label, preset_name, pc_number, user_modified FROM presets"
            ).fetchall()
        }
        assert rows["crunch"] == ("crunch", "Crunch", 9, 1)
        assert rows["clean"] == ("clean", "Clean", 0, 0)
        assert rows["edge"] == ("edge", "Edge of Breakup", 1, 0)
        assert rows["overdrive"] == ("overdrive", "Overdrive", 2, 0)
        assert rows["metal"] == ("metal", "Metal", 4, 0)
        assert rows["other"] == ("other", "Other", -1, 0)
        dupes = conn.execute(
            "SELECT pc_number FROM presets WHERE pc_number >= 0 "
            "GROUP BY pc_number HAVING COUNT(*) > 1"
        ).fetchall()
        assert dupes == []
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


# Test 4b — the clash variant: a user_modified row contests a default's PC.
def test_v10_user_modified_row_displaces_a_default():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v9_db_with_presets(
            path,
            [
                ("other", "Other", -1, 0),
                ("clean", "Clean", 0, 0),
                ("edge", "Edge of Breakup", 1, 0),
                ("overdrive", "Overdrive", 4, 0),
                ("crunch", "Crunch", 3, 0),
                ("metal", "Rectifier", 2, 1),
            ],
        )
        conn = init_db(path)
        rows = {
            r[0]: r
            for r in conn.execute(
                "SELECT tone_label, preset_name, pc_number, user_modified FROM presets"
            ).fetchall()
        }
        assert rows["metal"] == ("metal", "Rectifier", 2, 1)
        assert rows["overdrive"] == ("overdrive", "Overdrive", 4, 0)
        dupes = conn.execute(
            "SELECT pc_number FROM presets WHERE pc_number >= 0 "
            "GROUP BY pc_number HAVING COUNT(*) > 1"
        ).fetchall()
        assert dupes == []
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


# Test 5 — trap-1 regression: the exact 0922b7c reorder completes without raising.
def test_v10_trap1_regression_no_integrity_error():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = None
    try:
        _make_v3_db(
            path,
            version=6,
            presets=[
                ("other", "Other", -1), ("clean", "Clean", 0), ("crunch", "Crunch", 1),
                ("metal", "Metal", 2), ("edge", "Edge of Breakup", 3),
                ("overdrive", "Overdrive", 4),
            ],
        )
        conn = init_db(path)  # must not raise sqlite3.IntegrityError
        assert _presets_restricted_to_defaults(conn) == _EXPECTED_CONVERGED
    finally:
        if conn is not None:
            conn.close()
        os.unlink(path)


# Test 6 — _DEFAULT_PRESETS canary. Change this list only with a v11 migration
# that calls reconcile_presets — see ISSUE-006 §Lessons.
def test_default_presets_canary():
    assert _DEFAULT_PRESETS == [
        ("clean", "Clean", 0),
        ("edge", "Edge of Breakup", 1),
        ("overdrive", "Overdrive", 2),
        ("crunch", "Crunch", 3),
        ("metal", "Metal", 4),
        ("other", "Other", -1),
    ], (
        "_DEFAULT_PRESETS changed. Bump _CURRENT_VERSION and add a migration "
        "that calls reconcile_presets(conn), or every existing database "
        "diverges again (ISSUE-006)."
    )


# Test 7 — a fresh database has the index, the column, and every row unflagged.
def test_fresh_db_has_preset_index_and_user_modified_column(db):
    idx = db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_presets_pc'"
    ).fetchone()
    assert idx is not None
    cols = {r[1] for r in db.execute("PRAGMA table_info(presets)").fetchall()}
    assert "user_modified" in cols
    flags = {r[0] for r in db.execute("SELECT user_modified FROM presets").fetchall()}
    assert flags == {0}


# Test 8 — v10 is idempotent.
def test_v10_migration_idempotent(db):
    before = db.execute(
        "SELECT tone_label, preset_name, pc_number, user_modified FROM presets "
        "ORDER BY tone_label"
    ).fetchall()
    _migrate_v9_to_v10(db)
    after = db.execute(
        "SELECT tone_label, preset_name, pc_number, user_modified FROM presets "
        "ORDER BY tone_label"
    ).fetchall()
    assert before == after


# Test 9 — v10 is atomic: a failure mid-migration leaves the DB exactly at v9.
def test_v10_migration_atomic_on_failure(monkeypatch):
    import guitar_helper.db.schema as schema_module

    def _boom(conn):
        raise RuntimeError("simulated failure late in v10")

    monkeypatch.setattr(schema_module, "reconcile_presets", _boom)

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        _make_v3_db(
            path,
            version=6,
            presets=[
                ("other", "Other", -1), ("clean", "Clean", 0), ("crunch", "Crunch", 1),
                ("metal", "Metal", 2), ("edge", "Edge of Breakup", 3),
                ("overdrive", "Overdrive", 4),
            ],
        )
        with pytest.raises(RuntimeError):
            init_db(path)  # init_db closes its own connection on the raise path

        conn = sqlite3.connect(path)
        try:
            assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 9
            cols = {r[1] for r in conn.execute("PRAGMA table_info(presets)").fetchall()}
            assert "user_modified" not in cols
            idx = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_presets_pc'"
            ).fetchone()
            assert idx is None
            rows = conn.execute(
                "SELECT tone_label, pc_number FROM presets ORDER BY pc_number"
            ).fetchall()
            assert rows == [
                ("other", -1), ("clean", 0), ("crunch", 1),
                ("metal", 2), ("edge", 3), ("overdrive", 4),
            ]
        finally:
            conn.close()
    finally:
        os.unlink(path)


def test_default_preset_map_matches_default_presets():
    assert default_preset_map() == {tone: pc for tone, _name, pc in _DEFAULT_PRESETS}


def test_reconcile_presets_importable_from_schema_module():
    # db/repository.py imports this by name (D6) — a regression guard against
    # an accidental leading-underscore rename.
    assert callable(reconcile_presets)


# --- downgrade guard and pre-migration backup --------------------------------


def _seed_db_at_version(path: str, version: int) -> None:
    """Write a minimal database claiming an arbitrary schema version."""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
            (version, utcnow()),
        )
        conn.commit()
    finally:
        conn.close()


def test_init_db_rejects_a_newer_schema(tmp_path):
    path = tmp_path / "future.db"
    _seed_db_at_version(str(path), _CURRENT_VERSION + 1)

    with pytest.raises(SchemaTooNewError) as excinfo:
        init_db(str(path))

    assert excinfo.value.found == _CURRENT_VERSION + 1
    assert excinfo.value.supported == _CURRENT_VERSION


def test_rejected_newer_schema_is_left_untouched(tmp_path):
    path = tmp_path / "future.db"
    _seed_db_at_version(str(path), _CURRENT_VERSION + 1)
    before = path.read_bytes()

    with pytest.raises(SchemaTooNewError):
        init_db(str(path))

    # No DDL ran, so none of this build's tables were created on a database it
    # does not understand.
    assert path.read_bytes() == before
    conn = sqlite3.connect(str(path))
    try:
        assert _table_names(conn) == {"schema_version"}
    finally:
        conn.close()


def test_apply_migrations_guards_against_downgrade():
    # range() is empty when current > target, so the guard has to be explicit.
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(SchemaTooNewError):
            _apply_migrations(conn, _CURRENT_VERSION + 3)
    finally:
        conn.close()


def test_current_version_db_opens_without_a_backup(tmp_path):
    path = tmp_path / "library.db"
    init_db(str(path)).close()
    init_db(str(path)).close()
    assert list(tmp_path.glob("*.bak-v*")) == []


def test_migration_writes_a_backup(tmp_path):
    path = tmp_path / "library.db"
    _seed_db_at_version(str(path), 9)

    conn = init_db(str(path))
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == _CURRENT_VERSION
    finally:
        conn.close()

    backups = list(tmp_path.glob("library.db.bak-v9-to-v10-*"))
    assert len(backups) == 1
    # The snapshot must be the pre-migration state, not a copy of the result.
    snapshot = sqlite3.connect(str(backups[0]))
    try:
        assert snapshot.execute("SELECT version FROM schema_version").fetchone()[0] == 9
    finally:
        snapshot.close()


def test_backups_are_pruned_to_the_retention_limit(tmp_path):
    path = tmp_path / "library.db"
    for i in range(_BACKUP_RETENTION + 3):
        stale = tmp_path / f"library.db.bak-v9-to-v10-20260101-0000{i:02d}"
        stale.write_bytes(b"")
    _seed_db_at_version(str(path), 9)

    init_db(str(path)).close()

    assert len(list(tmp_path.glob("library.db.bak-v*"))) == _BACKUP_RETENTION


def test_pruning_leaves_hand_made_backups_alone(tmp_path):
    path = tmp_path / "library.db"
    manual = tmp_path / "library.db.bak-precal-20260624-153521"
    manual.write_bytes(b"")
    for i in range(_BACKUP_RETENTION + 2):
        (tmp_path / f"library.db.bak-v9-to-v10-20260101-0000{i:02d}").write_bytes(b"")
    _seed_db_at_version(str(path), 9)

    init_db(str(path)).close()

    assert manual.exists()


def test_in_memory_db_needs_no_backup_path():
    # _is_file_db must short-circuit; sqlite3 would otherwise be handed a
    # nonsense ":memory:.bak-..." destination.
    conn = init_db(":memory:")
    try:
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == _CURRENT_VERSION
    finally:
        conn.close()
