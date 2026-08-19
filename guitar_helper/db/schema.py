import sqlite3
from datetime import datetime, timezone

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS presets (
    tone_label    TEXT PRIMARY KEY,
    preset_name   TEXT NOT NULL,
    pc_number     INTEGER NOT NULL,
    user_modified INTEGER NOT NULL DEFAULT 0
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

CREATE TABLE IF NOT EXISTS segments_calibration (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash          TEXT NOT NULL REFERENCES tracks(file_hash),
    start_ms           INTEGER NOT NULL,
    end_ms             INTEGER NOT NULL,
    tone_label         TEXT NOT NULL,
    confidence         REAL NOT NULL,
    manually_corrected INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_segcal_hash
    ON segments_calibration(file_hash);

CREATE TABLE IF NOT EXISTS playlists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    file_hash   TEXT NOT NULL REFERENCES tracks(file_hash),
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, file_hash)
);

CREATE INDEX IF NOT EXISTS idx_pt_order
    ON playlist_tracks(playlist_id, position);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_CURRENT_VERSION = 10

_PARK_OFFSET = 1000
_NO_DISPATCH_PC = -1

_PRESET_PC_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_presets_pc "
    "ON presets(pc_number) WHERE pc_number >= 0"
)

_DEFAULT_PRESETS = [
    ("clean",     "Clean",            0),
    ("edge",      "Edge of Breakup",  1),
    ("overdrive", "Overdrive",        2),
    ("crunch",    "Crunch",           3),
    ("metal",     "Metal",            4),
    ("other",     "Other",           -1),  # -1 = no MIDI dispatch
]
# PC order is a deliberate clean->metal gain progression: clean, edge,
# overdrive, crunch, metal. 'ambient' deferred — no calibration data. Re-add a
# seed row + a re-seed migration to restore it on a free PC.


def init_db(path: str) -> sqlite3.Connection:
    """Open (or create) the database, apply schema, seed presets."""
    conn = sqlite3.connect(path)
    try:
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
    except Exception:
        # A failed seed/migration must not leak the connection — on Windows
        # the file stays locked until GC, which can block a retry or a
        # restore-from-backup in the same process.
        conn.close()
        raise

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


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    # Calibration snapshot table (Phase 4 O3): lazy pre-edit copy of segments.
    # executescript on _DDL is idempotent, so re-running the CREATEs is safe.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS segments_calibration (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash          TEXT NOT NULL REFERENCES tracks(file_hash),
            start_ms           INTEGER NOT NULL,
            end_ms             INTEGER NOT NULL,
            tone_label         TEXT NOT NULL,
            confidence         REAL NOT NULL,
            manually_corrected INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_segcal_hash
            ON segments_calibration(file_hash);
        """
    )


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    # Playlists (Phase 4 O2).
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS playlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS playlist_tracks (
            playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
            file_hash   TEXT NOT NULL REFERENCES tracks(file_hash),
            position    INTEGER NOT NULL,
            PRIMARY KEY (playlist_id, file_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_pt_order
            ON playlist_tracks(playlist_id, position);
        """
    )


def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    # App settings (Phase 4 O4) — key/value so a new knob never needs a column.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def default_preset_map() -> dict[str, int]:
    """Canonical tone -> PC. The UI reads this instead of hardcoding numbers."""
    return {tone: pc for tone, _name, pc in _DEFAULT_PRESETS}


def reconcile_presets(conn: sqlite3.Connection) -> None:
    """Move every non-user-modified preset row onto _DEFAULT_PRESETS.

    The caller owns the transaction. Rows with user_modified = 1 are never
    touched; rows for tones the app no longer knows keep their PC if it is
    free and are parked at no-dispatch if it is not.
    """
    conn.executemany(
        "INSERT OR IGNORE INTO presets(tone_label, preset_name, pc_number) VALUES (?,?,?)",
        _DEFAULT_PRESETS,
    )
    # SQLite enforces UNIQUE row-by-row inside an UPDATE, so a reorder collides
    # mid-statement even when the end state is valid. Park out of range first.
    conn.execute(
        "UPDATE presets SET pc_number = pc_number + ? "
        "WHERE user_modified = 0 AND pc_number >= 0",
        (_PARK_OFFSET,),
    )
    for tone, name, pc in _DEFAULT_PRESETS:
        conn.execute(
            "UPDATE presets SET preset_name = ?, pc_number = ? "
            "WHERE tone_label = ? AND user_modified = 0",
            (name, pc, tone),
        )
    _relocate_unmapped_presets(conn)

    dupes = [
        r[0]
        for r in conn.execute(
            "SELECT pc_number FROM presets WHERE pc_number >= 0 "
            "GROUP BY pc_number HAVING COUNT(*) > 1"
        )
    ]
    if dupes:
        raise sqlite3.IntegrityError(
            f"preset reconcile left duplicate pc_number(s): {dupes}"
        )


def _relocate_unmapped_presets(conn: sqlite3.Connection) -> None:
    """Unpark leftovers and break every remaining pc_number tie.

    Precedence: a user-modified row keeps its PC; then a tone in
    _DEFAULT_PRESETS; anything else yields. A displaced default takes the
    lowest free PC, a displaced leftover goes to no-dispatch.
    """
    default_tones = {tone for tone, _name, _pc in _DEFAULT_PRESETS}
    rows = conn.execute(
        "SELECT tone_label, pc_number, user_modified FROM presets"
    ).fetchall()

    def rank(row: tuple) -> tuple:
        tone, _pc, user_modified = row
        return (0 if user_modified else 1, 0 if tone in default_tones else 1, tone)

    taken: set[int] = set()
    moves: list[tuple[int, str]] = []
    for tone, pc, _user_modified in sorted(rows, key=rank):
        if pc < 0:
            continue
        parked = pc >= _PARK_OFFSET
        wanted = pc - _PARK_OFFSET if parked else pc
        if wanted in taken:
            wanted = _NO_DISPATCH_PC if parked else _lowest_free_pc(taken)
        if wanted >= 0:
            taken.add(wanted)
        if wanted != pc:
            moves.append((wanted, tone))
    if moves:
        conn.executemany(
            "UPDATE presets SET pc_number = ? WHERE tone_label = ?", moves
        )


def _lowest_free_pc(taken: set[int]) -> int:
    pc = 0
    while pc in taken:
        pc += 1
    return pc


def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    # Explicit BEGIN, not just `with conn:` — sqlite3 opens an implicit
    # transaction before DML but not before DDL, so ALTER TABLE and
    # CREATE INDEX would otherwise survive a rollback.
    conn.execute("BEGIN")
    with conn:
        _add_column_if_missing(
            conn, "presets", "user_modified", "INTEGER NOT NULL DEFAULT 0"
        )
        reconcile_presets(conn)
        conn.execute(_PRESET_PC_INDEX_DDL)


_MIGRATIONS = {
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
    4: _migrate_v3_to_v4,
    5: _migrate_v4_to_v5,
    6: _migrate_v5_to_v6,
    7: _migrate_v6_to_v7,
    8: _migrate_v7_to_v8,
    9: _migrate_v8_to_v9,
    10: _migrate_v9_to_v10,
}


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO presets(tone_label, preset_name, pc_number) VALUES (?,?,?)",
        _DEFAULT_PRESETS,
    )
    conn.execute(_PRESET_PC_INDEX_DDL)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
