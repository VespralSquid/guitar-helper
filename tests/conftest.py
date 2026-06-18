from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from guitar_helper.db.interfaces import Segment
from guitar_helper.db.repository import SQLiteSegmentStore
from guitar_helper.db.schema import init_db


@pytest.fixture
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def store(db):
    return SQLiteSegmentStore(db)


@pytest.fixture
def track_hash(db):
    db.execute(
        "INSERT INTO tracks(file_hash, filename, duration_ms, analysed_at) VALUES (?,?,?,?)",
        ("abc123", "test.wav", 60000, "2026-01-01T00:00:00+00:00"),
    )
    db.commit()
    return "abc123"


@pytest.fixture
def make_wav(tmp_path):
    def _make(filename: str = "test.wav", duration_s: float = 2.0, sr: int = 22050) -> Path:
        t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
        y = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        path = tmp_path / filename
        sf.write(str(path), y, sr)
        return path
    return _make


def make_segment(
    file_hash: str,
    start_ms: int,
    end_ms: int,
    tone_label: str = "clean",
    confidence: float = 0.9,
    manually_corrected: bool = False,
) -> Segment:
    return Segment(
        id=0,
        file_hash=file_hash,
        start_ms=start_ms,
        end_ms=end_ms,
        tone_label=tone_label,
        confidence=confidence,
        manually_corrected=manually_corrected,
    )
