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
    calibration_excluded INTEGER NOT NULL DEFAULT 0
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

_CURRENT_VERSION = 2

_DEFAULT_PRESETS = [
    ("clean",   "Clean",            0),
    ("edge",    "Edge of Breakup",  4),
    ("crunch",  "Crunch",           1),
    ("metal",   "Metal",            2),
    ("ambient", "Ambient",          3),
    ("other",   "Other",           -1),  # -1 = no MIDI dispatch
]


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
    elif row[0] < 2:
        _migrate_v1_to_v2(conn)

    return conn


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    existing = [r[1] for r in conn.execute("PRAGMA table_info(tracks)").fetchall()]
    if "calibration_excluded" not in existing:
        conn.execute(
            "ALTER TABLE tracks ADD COLUMN calibration_excluded INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute("UPDATE schema_version SET version = 2, applied_at = ?", (utcnow(),))
    conn.commit()


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO presets(tone_label, preset_name, pc_number) VALUES (?,?,?)",
        _DEFAULT_PRESETS,
    )


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
