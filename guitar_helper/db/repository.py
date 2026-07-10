from __future__ import annotations

import sqlite3

from .interfaces import ISegmentStore, Preset, Segment, Track
from .schema import utcnow


class SQLiteSegmentStore(ISegmentStore):

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
        self._conn.commit()

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
            INSERT INTO presets(tone_label, preset_name, pc_number)
            VALUES (?, ?, ?)
            ON CONFLICT(tone_label) DO UPDATE
                SET preset_name = excluded.preset_name,
                    pc_number   = excluded.pc_number
            """,
            (preset.tone_label, preset.preset_name, preset.pc_number),
        )
        self._conn.commit()

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
            """
            SELECT t.file_hash, t.filename, t.title, t.artist, t.duration_ms,
                   t.source_path, t.calibration_excluded,
                   COALESCE(SUM(s.manually_corrected), 0) AS corrected,
                   COUNT(s.id) AS total
            FROM tracks t
            LEFT JOIN segments s ON s.file_hash = t.file_hash
            GROUP BY t.file_hash
            ORDER BY t.artist, t.title, t.filename
            """
        ).fetchall()
        return [
            Track(
                file_hash=r[0],
                filename=r[1],
                title=r[2],
                artist=r[3],
                duration_ms=r[4],
                source_path=r[5],
                calibration_excluded=bool(r[6]),
                corrected_count=r[7],
                total_count=r[8],
            )
            for r in rows
        ]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

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
