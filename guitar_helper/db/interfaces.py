from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Segment:
    id: int
    file_hash: str
    start_ms: int
    end_ms: int
    tone_label: str
    confidence: float
    manually_corrected: bool


@dataclass
class Preset:
    tone_label: str
    preset_name: str
    pc_number: int  # -1 means no MIDI dispatch (used for 'other')


@dataclass
class Track:
    file_hash: str
    filename: str
    title: str | None
    artist: str | None
    duration_ms: int
    source_path: str | None
    calibration_excluded: bool
    corrected_count: int   # manually_corrected=1 segments
    total_count: int       # all segments; needs-labeling = corrected_count < total_count
    analysed_at: str = ""  # ISO timestamp; shown as "Date added" in the UI


@dataclass
class Playlist:
    id: int
    name: str
    created_at: str
    track_count: int


class ITrackCatalog(ABC):
    """Track metadata: library listing + calibration-exclusion flag."""

    @abstractmethod
    def save_track(
        self,
        file_hash: str,
        filename: str,
        title: str | None,
        artist: str | None,
        duration_ms: int,
        source_path: str | None = None,
    ) -> None:
        """Insert track metadata, refreshing it (incl. source_path) on re-analysis."""

    @abstractmethod
    def set_calibration_excluded(self, file_hash: str, excluded: bool) -> None:
        """Mark or unmark a track as excluded from calibration."""

    @abstractmethod
    def get_calibration_excluded(self, file_hash: str) -> bool:
        """Return True if the track is excluded from calibration."""

    @abstractmethod
    def list_tracks(self) -> list[Track]:
        """Return every analysed track with correction-progress counts."""


class ISegmentReader(ABC):
    """Read-only segment access — playback and lookup paths only need this."""

    @abstractmethod
    def get_segment(self, file_hash: str, position_ms: int) -> Segment | None:
        """Return the segment active at position_ms, or None."""

    @abstractmethod
    def get_segments(self, file_hash: str) -> list[Segment]:
        """Return all segments for a track, ordered by start_ms."""


class ISegmentEditor(ABC):
    """Segment writes: bulk replace (analysis) and single-row correction."""

    @abstractmethod
    def save_segments(self, file_hash: str, segments: list[Segment]) -> None:
        """Persist (or replace) all segments for a track."""

    @abstractmethod
    def update_segment(self, segment: Segment) -> None:
        """Write corrected fields back for a single segment row."""

    @abstractmethod
    def delete_segment(self, segment_id: int) -> None:
        """Delete a single segment row (used by merge to remove absorbed segments)."""

    @abstractmethod
    def ensure_calibration_copy(self, file_hash: str) -> None:
        """Snapshot this track's current segments into segments_calibration,
        but only if no snapshot exists yet — idempotent, never overwrites."""

    @abstractmethod
    def get_calibration_segments(self, file_hash: str) -> list[Segment]:
        """Return the pre-edit calibration snapshot for a track, or [] if
        ensure_calibration_copy has never been called for it."""

    def apply_edits(
        self, file_hash: str, updated: list[Segment], deleted_ids: list[int]
    ) -> None:
        """Persist one edit session — the calibration snapshot, the updates and
        the deletions — as a single unit.

        This default is a convenience for in-memory implementers and is NOT
        atomic. Any implementer that owns a transaction must override it.
        """
        self.ensure_calibration_copy(file_hash)
        for segment in updated:
            self.update_segment(segment)
        for segment_id in deleted_ids:
            self.delete_segment(segment_id)


class IPresetStore(ABC):
    """Tone-to-PC preset mapping — the MIDI dispatcher's only dependency."""

    @abstractmethod
    def get_presets(self) -> list[Preset]:
        """Return all tone presets."""

    @abstractmethod
    def save_preset(self, preset: Preset) -> None:
        """Insert or replace a preset row."""

    def reset_presets_to_defaults(self) -> None:
        """Clear every user_modified flag and re-derive the table from the
        seeded defaults. Backs the Output-mode divergence banner."""
        raise NotImplementedError


class ISettingsStore(ABC):
    """Persisted app-level knobs (Phase 4 O4). Key/value, so adding a knob is
    never a schema change. Values are strings; typed accessors do the parsing
    and fall back to the default when a key is absent or malformed."""

    @abstractmethod
    def get_setting(self, key: str) -> str | None:
        """Return the stored value for key, or None if never set."""

    @abstractmethod
    def set_setting(self, key: str, value: str) -> None:
        """Insert or replace the value for key."""

    @abstractmethod
    def get_int_setting(self, key: str, default: int) -> int:
        """Return key as an int, or default if unset or unparseable."""


class IPlaylistStore(ABC):
    """Playlist CRUD + ordered membership (Phase 4 O2). The queue itself is
    in-memory session state — only playlists persist."""

    @abstractmethod
    def create_playlist(self, name: str) -> int:
        """Create a playlist, returning its id. Name must be unique."""

    @abstractmethod
    def delete_playlist(self, playlist_id: int) -> None:
        """Delete a playlist and its membership rows."""

    @abstractmethod
    def list_playlists(self) -> list[Playlist]:
        """Return all playlists with track counts, ordered by name."""

    @abstractmethod
    def add_to_playlist(self, playlist_id: int, file_hash: str) -> None:
        """Append a track to a playlist; duplicate membership is a no-op."""

    @abstractmethod
    def remove_from_playlist(self, playlist_id: int, file_hash: str) -> None:
        """Remove a track from a playlist."""

    @abstractmethod
    def get_playlist_tracks(self, playlist_id: int) -> list[Track]:
        """Return the playlist's tracks in position order."""


class ISegmentStore(ITrackCatalog, ISegmentReader, ISegmentEditor, IPresetStore):
    """Deprecated combined alias, kept for consumers that genuinely span
    multiple roles (analysis pipeline, correction CLI).
    New code should type-hint the specific role ABC it needs instead."""


class IAppStore(ISegmentStore, ISettingsStore):
    """What the composition root binds: the segment/track/preset roles plus
    persisted settings. Kept separate from ISegmentStore so implementers that
    only handle segments (test doubles, the analysis pipeline) are not forced
    to grow settings methods they never call."""
