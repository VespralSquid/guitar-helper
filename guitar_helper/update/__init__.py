"""Update channel. Qt-free by design — see ui/update_worker.py for the Qt surface.

Flow, per docs/new feature specs/Packaging_and_Update_Strategy.md section 6:

    manifest (HTTPS) -> version compare -> download to temp -> verify SHA-256
    -> launch installer /SILENT -> exit

The verification step is the security boundary. Anyone who can serve a modified
download gets code execution as the user, so the digest is checked before the
file is ever handed to the OS, and a mismatch deletes the download.
"""
from .manifest import (
    DEFAULT_MANIFEST_URL,
    UpdateError,
    UpdateManifest,
    UpdateVerificationError,
    current_version,
    fetch_manifest,
    is_newer,
    parse_manifest,
    parse_version,
)
from .service import check_for_update, download_update, launch_installer

__all__ = [
    "DEFAULT_MANIFEST_URL",
    "UpdateError",
    "UpdateManifest",
    "UpdateVerificationError",
    "check_for_update",
    "current_version",
    "download_update",
    "fetch_manifest",
    "is_newer",
    "launch_installer",
    "parse_manifest",
    "parse_version",
]
