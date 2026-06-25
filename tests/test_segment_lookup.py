from __future__ import annotations

from guitar_helper.playback.segment_lookup import SegmentLookup
from tests.conftest import make_segment


def test_lookup_at_and_all(store, track_hash):
    store.save_segments(track_hash, [
        make_segment(track_hash, 0, 1000, "clean"),
        make_segment(track_hash, 1000, 2000, "metal"),
    ])
    lookup = SegmentLookup(store, track_hash)

    assert lookup.at(500).tone_label == "clean"
    assert lookup.at(1500).tone_label == "metal"
    assert lookup.at(5000) is None
    assert [s.tone_label for s in lookup.all()] == ["clean", "metal"]
