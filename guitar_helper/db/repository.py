from __future__ import annotations

import sqlite3

from .interfaces import IAppStore, IPlaylistStore, Playlist, Preset, Segment, Track
from .schema import reconcile_presets, utcnow

_TRACK_SELECT = """
    SELECT t.file_hash, t.filename, t.title, t.artist, t.duration_ms,
           t.source_path, t.calibration_excluded, t.analysed_at,
           COALESCE(SUM(s.manually_corrected), 0) AS corrected,
           COUNT(s.id) AS total
    FROM tracks t
    LEFT JOIN segments s ON s.file_hash = t.file_hash
"""

_UPDATE_SEGMENT_SQL = """
    UPDATE segments
    SET start_ms = ?, end_ms = ?, tone_label = ?,
        confidence = ?, manually_corrected = ?
    WHERE id = ?
"""

_DELETE_SEGMENT_SQL = "DELETE FROM segments WHERE id = ?"

_CALIBRATION_COPY_SQL = """
    INSERT INTO segments_calibration
        (file_hash, start_ms, end_ms, tone_label, confidence, manually_corrected)
    SELECT file_hash, start_ms, end_ms, tone_label, confidence, manually_corrected
    FROM segments
    WHERE file_hash = ?
      AND NOT EXISTS (SELECT 1 FROM segments_calibration WHERE file_hash = ?)
"""


class SQLiteSegmentStore(IAppStore, IPlaylistStore):

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Segments
    # ------------------------------------------------------------------

    def get_segment(self, file_hash: str, position_ms: int) -> Segment | None:
        row = self._conn.execute(
            """
            SELECT id, file_hash, start_ms, end_ms, tone_label,
                   confidence, manually_corrected
            FROM segments
            WHERE file_hash = ?
              AND start_ms  <= ?
              AND end_ms    >  ?
            ORDER BY start_ms DESC
            LIMIT 1
            """,
            (file_hash, position_ms, position_ms),
        ).fetchone()
        return _row_to_segment(row) if row else None

    def save_segments(self, file_hash: str, segments: list[Segment]) -> None:
        with self._conn:  # rolls back the DELETE if any row fails
            self._conn.execute(
                "DELETE FROM segments WHERE file_hash = ?", (file_hash,)
            )
            self._conn.executemany(
                """
                INSERT INTO segments
                    (file_hash, start_ms, end_ms, tone_label,
                     confidence, manually_corrected)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        file_hash,
                        s.start_ms,
                        s.end_ms,
                        s.tone_label,
                        s.confidence,
                        int(s.manually_corrected),
                    )
                    for s in segments
                ],
            )

    def apply_edits(
        self, file_hash: str, updated: list[Segment], deleted_ids: list[int]
    ) -> None:
        with self._conn:
            self._conn.execute(_CALIBRATION_COPY_SQL, (file_hash, file_hash))
            self._conn.executemany(
                _UPDATE_SEGMENT_SQL,
                [
                    (s.start_ms, s.end_ms, s.tone_label, s.confidence,
                     int(s.manually_corrected), s.id)
                    for s in updated
                ],
            )
            self._conn.executemany(_DELETE_SEGMENT_SQL, [(i,) for i in deleted_ids])

    def update_segment(self, segment: Segment) -> None:
        self._conn.execute(
            """
            UPDATE segments
            SET start_ms = ?, end_ms = ?, tone_label = ?,
                confidence = ?, manually_corrected = ?
            WHERE id = ?
            """,
            (
                segment.start_ms,
                segment.end_ms,
                segment.tone_label,
                segment.confidence,
                int(segment.manually_corrected),
                segment.id,
            ),
        )
        self._conn.commit()

    def get_segments(self, file_hash: str) -> list[Segment]:
        rows = self._conn.execute(
            """
            SELECT id, file_hash, start_ms, end_ms, tone_label,
                   confidence, manually_corrected
            FROM segments
            WHERE file_hash = ?
            ORDER BY start_ms
            """,
            (file_hash,),
        ).fetchall()
        return [_row_to_segment(r) for r in rows]

    def delete_segment(self, segment_id: int) -> None:
        self._conn.execute("DELETE FROM segments WHERE id = ?", (segment_id,))
        self._conn.commit()

    def ensure_calibration_copy(self, file_hash: str) -> None:
        self._conn.execute(
            """
            INSERT INTO segments_calibration
                (file_hash, start_ms, end_ms, tone_label,
                 confidence, manually_corrected)
            SELECT file_hash, start_ms, end_ms, tone_label,
                   confidence, manually_corrected
            FROM segments
            WHERE file_hash = ?
              AND NOT EXISTS (
                  SELECT 1 FROM segments_calibration WHERE file_hash = ?
              )
            """,
            (file_hash, file_hash),
        )
        self._conn.commit()

    def get_calibration_segments(self, file_hash: str) -> list[Segment]:
        rows = self._conn.execute(
            """
            SELECT id, file_hash, start_ms, end_ms, tone_label,
                   confidence, manually_corrected
            FROM segments_calibration
            WHERE file_hash = ?
            ORDER BY start_ms
            """,
            (file_hash,),
        ).fetchall()
        return [_row_to_segment(r) for r in rows]

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def get_presets(self) -> list[Preset]:
        rows = self._conn.execute(
            "SELECT tone_label, preset_name, pc_number FROM presets ORDER BY pc_number"
        ).fetchall()
        return [Preset(tone_label=r[0], preset_name=r[1], pc_number=r[2]) for r in rows]

    def save_preset(self, preset: Preset) -> None:
        self._conn.execute(
            """
            INSERT INTO presets(tone_label, preset_name, pc_number, user_modified)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(tone_label) DO UPDATE
                SET preset_name   = excluded.preset_name,
                    pc_number     = excluded.pc_number,
                    user_modified = 1
            """,
            (preset.tone_label, preset.preset_name, preset.pc_number),
        )
        self._conn.commit()

    def reset_presets_to_defaults(self) -> None:
        with self._conn:
            self._conn.execute("UPDATE presets SET user_modified = 0")
            reconcile_presets(self._conn)

    def save_track(
        self,
        file_hash: str,
        filename: str,
        title: str | None,
        artist: str | None,
        duration_ms: int,
        source_path: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO tracks
                (file_hash, filename, title, artist, duration_ms, analysed_at, source_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_hash) DO UPDATE SET
                filename    = excluded.filename,
                title       = excluded.title,
                artist      = excluded.artist,
                duration_ms = excluded.duration_ms,
                analysed_at = excluded.analysed_at,
                source_path = excluded.source_path
            """,
            (file_hash, filename, title, artist, duration_ms, utcnow(), source_path),
        )
        self._conn.commit()

    def set_calibration_excluded(self, file_hash: str, excluded: bool) -> None:
        self._conn.execute(
            "UPDATE tracks SET calibration_excluded = ? WHERE file_hash = ?",
            (1 if excluded else 0, file_hash),
        )
        self._conn.commit()

    def get_calibration_excluded(self, file_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT calibration_excluded FROM tracks WHERE file_hash = ?",
            (file_hash,),
        ).fetchone()
        return bool(row[0]) if row else False

    def list_tracks(self) -> list[Track]:
        rows = self._conn.execute(
            _TRACK_SELECT + """
            GROUP BY t.file_hash
            ORDER BY t.artist, t.title, t.filename
            """
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    def create_playlist(self, name: str) -> int:
        cursor = self._conn.execute(
            "INSERT INTO playlists(name, created_at) VALUES (?, ?)",
            (name, utcnow()),
        )
        self._conn.commit()
        return cursor.lastrowid

    def delete_playlist(self, playlist_id: int) -> None:
        self._conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self._conn.commit()

    def list_playlists(self) -> list[Playlist]:
        rows = self._conn.execute(
            """
            SELECT p.id, p.name, p.created_at, COUNT(pt.file_hash)
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id
            GROUP BY p.id
            ORDER BY p.name
            """
        ).fetchall()
        return [Playlist(id=r[0], name=r[1], created_at=r[2], track_count=r[3]) for r in rows]

    def add_to_playlist(self, playlist_id: int, file_hash: str) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO playlist_tracks(playlist_id, file_hash, position)
            SELECT ?, ?, COALESCE(MAX(position), -1) + 1
            FROM playlist_tracks WHERE playlist_id = ?
            """,
            (playlist_id, file_hash, playlist_id),
        )
        self._conn.commit()

    def remove_from_playlist(self, playlist_id: int, file_hash: str) -> None:
        self._conn.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ? AND file_hash = ?",
            (playlist_id, file_hash),
        )
        self._conn.commit()

    def get_playlist_tracks(self, playlist_id: int) -> list[Track]:
        rows = self._conn.execute(
            _TRACK_SELECT + """
            JOIN playlist_tracks pt ON pt.file_hash = t.file_hash
            WHERE pt.playlist_id = ?
            GROUP BY t.file_hash
            ORDER BY pt.position
            """,
            (playlist_id,),
        ).fetchall()
        return [_row_to_track(r) for r in rows]

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self._conn.commit()

    def get_int_setting(self, key: str, default: int) -> int:
        raw = self.get_setting(key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _row_to_track(r: tuple) -> Track:
    return Track(
        file_hash=r[0],
        filename=r[1],
        title=r[2],
        artist=r[3],
        duration_ms=r[4],
        source_path=r[5],
        calibration_excluded=bool(r[6]),
        analysed_at=r[7],
        corrected_count=r[8],
        total_count=r[9],
    )


def _row_to_segment(row: tuple) -> Segment:
    return Segment(
        id=row[0],
        file_hash=row[1],
        start_ms=row[2],
        end_ms=row[3],
        tone_label=row[4],
        confidence=row[5],
        manually_corrected=bool(row[6]),
    )
