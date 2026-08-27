"""Check, download and hand off to the installer."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from .manifest import (
    UpdateError,
    UpdateManifest,
    UpdateVerificationError,
    current_version,
    fetch_manifest,
)

_CHUNK = 256 * 1024

# Inno Setup switches. CLOSEAPPLICATIONS lets the installer replace files the
# outgoing build still holds open; RESTARTAPPLICATIONS is off because this
# process exits immediately and the installer would otherwise start a second
# copy behind the new one.
_INSTALLER_ARGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]

ProgressFn = Callable[[int, int], None]


def check_for_update(
    url: str | None = None, *, current: str | None = None, timeout: float = 10.0
) -> UpdateManifest | None:
    """Return a newer manifest, or None when already current.

    Raises UpdateError on a network or manifest problem; callers on the startup
    path are expected to swallow it. A failed update check must never stop the
    app from starting.
    """
    manifest = fetch_manifest(url, timeout=timeout)
    if not manifest.is_newer_than(current or current_version()):
        return None
    return manifest


def download_update(
    manifest: UpdateManifest,
    *,
    dest_dir: str | Path | None = None,
    timeout: float = 30.0,
    progress: ProgressFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> Path:
    """Stream the installer to disk and verify it against the manifest digest.

    The digest is computed while streaming rather than by re-reading the file,
    so there is no window in which a verified path on disk holds unverified
    bytes. A mismatch deletes the download and raises.
    """
    import requests  # noqa: PLC0415

    directory = Path(dest_dir) if dest_dir else Path(tempfile.gettempdir()) / "guitar-helper-update"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"GuitarHelper-Setup-{manifest.version}.exe"

    digest = hashlib.sha256()
    downloaded = 0
    try:
        with requests.get(manifest.url, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            with open(target, "wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK):
                    if should_cancel is not None and should_cancel():
                        raise UpdateError("update download cancelled")
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(downloaded, total)
    except requests.RequestException as exc:
        _discard(target)
        raise UpdateError(f"download failed: {exc}") from exc
    except BaseException:
        _discard(target)
        raise

    actual = digest.hexdigest()
    if actual != manifest.sha256:
        _discard(target)
        raise UpdateVerificationError(
            f"downloaded installer does not match the manifest digest "
            f"(expected {manifest.sha256}, got {actual}) — discarded"
        )
    return target


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def launch_installer(path: str | Path, *, args: list[str] | None = None) -> None:
    """Start the verified installer and return so the caller can exit.

    Windows will not let the installer overwrite this process's own exe while
    it runs, so the handoff is: spawn detached, exit immediately, let the
    installer do the replacing. DETACHED_PROCESS keeps the child alive past our
    exit; without it the installer dies with the parent console.
    """
    installer = Path(path)
    if not installer.is_file():
        raise UpdateError(f"installer not found: {installer}")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )

    try:
        subprocess.Popen(  # noqa: S603 — path is ours, verified above
            [str(installer), *(args if args is not None else _INSTALLER_ARGS)],
            close_fds=True,
            creationflags=creationflags,
            cwd=os.path.dirname(str(installer)) or None,
        )
    except OSError as exc:
        raise UpdateError(f"could not start the installer: {exc}") from exc
