from __future__ import annotations

from pathlib import Path

from .audio_loader import AudioLoader

_SUPPORTED = {".wav", ".flac", ".ogg", ".aiff", ".mp3", ".m4a", ".aac"}


def locate(
    file_hash: str,
    source_path: str | None,
    library_root: str | Path | None,
    loader: AudioLoader | None = None,
) -> Path | None:
    """Resolve a track to a playable file by content hash.

    Tries the stored source_path first (hash is the identity, so an existing
    path needs no re-verification). Falls back to walking library_root and
    re-hashing candidates — collision-proof where matching by basename is not.
    Returns None if nothing matches.
    """
    if source_path:
        p = Path(source_path)
        if p.exists():
            return p

    if library_root is None:
        return None

    root = Path(library_root)
    if not root.is_dir():
        return None

    loader = loader or AudioLoader()
    for candidate in sorted(root.rglob("*")):
        if candidate.suffix.lower() not in _SUPPORTED or not candidate.is_file():
            continue
        try:
            candidate_hash = loader.hash_file(candidate)
        except OSError:
            continue
        if candidate_hash == file_hash:
            return candidate

    return None
