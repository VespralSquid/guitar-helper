import pytest

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
