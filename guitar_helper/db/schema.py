import sqlite3
from datetime import datetime, timezone

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS presets (
    tone_label  TEXT PRIMARY KEY,
    preset_name TEXT NOT NULL,
    pc_number   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    file_hash            TEXT PRIMARY KEY,
    filename             TEXT NOT NULL,
    title                TEXT,
    artist               TEXT,
    duration_ms          INTEGER NOT NULL,
    analysed_at          TEXT NOT NULL,
    calibration_excluded INTEGER NOT NULL DEFAULT 0,
    source_path          TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash          TEXT NOT NULL REFERENCES tracks(file_hash),
    start_ms           INTEGER NOT NULL,
    end_ms             INTEGER NOT NULL,
    tone_label         TEXT NOT NULL REFERENCES presets(tone_label),
    confidence         REAL NOT NULL,
    manually_corrected INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_seg_lookup
    ON segments(file_hash, start_ms, end_ms);
"""

_CURRENT_VERSION = 6

_DEFAULT_PRESETS = [
    ("clean",     "Clean",            0),
    ("crunch",    "Crunch",           1),
    ("metal",     "Metal",            2),
    ("edge",      "Edge of Breakup",  3),
    ("overdrive", "Overdrive",        4),
    ("other",     "Other",           -1),  # -1 = no MIDI dispatch
]
# 'ambient' (was PC3) deferred — no calibration data. Re-add a seed row + a re-seed
# migration to restore it (pick a free PC; 0-3 are now clean/crunch/metal/edge).


def init_db(path: str) -> sqlite3.Connection:
    """Open (or create) the database, apply schema, seed presets."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL)

    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        _seed(conn)
        conn.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
            (_CURRENT_VERSION, utcnow()),
        )
        conn.commit()
    else:
        _apply_migrations(conn, row[0])

    return conn


def _apply_migrations(conn: sqlite3.Connection, current: int) -> None:
    # Apply every pending step in order so a DB any number of versions behind
    # reaches _CURRENT_VERSION. _MIGRATIONS[v] upgrades a DB at v-1 to v.
    for target in range(current + 1, _CURRENT_VERSION + 1):
        _MIGRATIONS[target](conn)
        conn.execute(
            "UPDATE schema_version SET version = ?, applied_at = ?", (target, utcnow())
        )
        conn.commit()


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    existing = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(
        conn, "tracks", "calibration_excluded", "INTEGER NOT NULL DEFAULT 0"
    )


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "tracks", "source_path", "TEXT")


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    # Drop the deferred 'ambient' preset, but only if nothing references it
    # (the segments FK must stay valid).
    conn.execute(
        """
        DELETE FROM presets
        WHERE tone_label = 'ambient'
          AND NOT EXISTS (SELECT 1 FROM segments WHERE tone_label = 'ambient')
        """
    )


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    # ambient vacated PC3; move edge into it so PCs 0-3 are contiguous.
    conn.execute("UPDATE presets SET pc_number = 3 WHERE tone_label = 'edge'")


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    # Add 'overdrive' — a mid-gain tone between edge and crunch.
    conn.execute(
        "INSERT OR IGNORE INTO presets(tone_label, preset_name, pc_number) "
        "VALUES ('overdrive', 'Overdrive', 4)"
    )


_MIGRATIONS = {
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
    4: _migrate_v3_to_v4,
    5: _migrate_v4_to_v5,
    6: _migrate_v5_to_v6,
}


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO presets(tone_label, preset_name, pc_number) VALUES (?,?,?)",
        _DEFAULT_PRESETS,
    )


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
