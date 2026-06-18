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


class ISegmentStore(ABC):

    @abstractmethod
    def get_segment(self, file_hash: str, position_ms: int) -> Segment | None:
        """Return the segment active at position_ms, or None."""

    @abstractmethod
    def save_segments(self, file_hash: str, segments: list[Segment]) -> None:
        """Persist (or replace) all segments for a track."""

    @abstractmethod
    def update_segment(self, segment: Segment) -> None:
        """Write corrected fields back for a single segment row."""

    @abstractmethod
    def get_segments(self, file_hash: str) -> list[Segment]:
        """Return all segments for a track, ordered by start_ms."""

    @abstractmethod
    def get_presets(self) -> list[Preset]:
        """Return all tone presets."""

    @abstractmethod
    def save_preset(self, preset: Preset) -> None:
        """Insert or replace a preset row."""

    @abstractmethod
    def save_track(
        self,
        file_hash: str,
        filename: str,
        title: str | None,
        artist: str | None,
        duration_ms: int,
    ) -> None:
        """Insert track metadata if not already present (idempotent)."""
