"""Single source of truth for resource locations.

Every path the app needs (database, audio library, stem cache, archetypes) is
derived from one `root`, so nothing depends on the process's current working
directory. `root` precedence: explicit argument > GUITAR_HELPER_HOME env var >
frozen-aware default.

The default differs by how the app is running. From a source checkout it is the
CWD, as it always was. From a frozen build (PyInstaller sets `sys.frozen`) the
CWD is wherever the shortcut happened to resolve to and the install directory is
read-only for a standard user, so the default becomes a per-user data directory.

That split is the reason `resource_path()` exists alongside `AppConfig`. Two
different lifetimes are involved and they must not share a root:

  * writable, per-user, survives an update — the database, the stem cache, the
    user's edited archetypes. `AppConfig`.
  * read-only, ships inside the bundle, replaced wholesale by every update —
    the default archetypes. `resource_path()`.

Resources are deliberately not reachable through `--root`: pointing the app at a
different data directory must not change which bundled defaults it reads.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

_ENV_HOME = "GUITAR_HELPER_HOME"

_APP_DIR_NAME = "GuitarHelper"

_DEFAULT_DB = "library.db"
_DEFAULT_LIBRARY = "music"
_DEFAULT_STEMS = "stems"
_DEFAULT_ARCHETYPES = "archetypes.json"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle rather than a source checkout."""
    return bool(getattr(sys, "frozen", False))


def user_data_dir() -> Path:
    """Per-user writable directory for the database, stem cache and user config.

    Windows uses %LOCALAPPDATA% rather than %APPDATA% deliberately: the stem
    cache is large and machine-specific, and roaming profiles should not try to
    sync it.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / _APP_DIR_NAME


def resource_path(name: str) -> Path:
    """Locate a read-only asset that ships with the app.

    PyInstaller unpacks bundled data under `sys._MEIPASS`; from a checkout the
    equivalent location is the repo root, one level above this package.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = Path(__file__).resolve().parent.parent
    return Path(base) / name


def _default_base() -> Path:
    return user_data_dir() if is_frozen() else Path.cwd()


@dataclass(frozen=True)
class AppConfig:
    root: Path
    db_path: Path
    library_root: Path
    stems_dir: Path
    archetypes_path: Path
    model_dir: Path | None

    @classmethod
    def resolve(
        cls,
        root: str | Path | None = None,
        *,
        db: str | Path | None = None,
        library: str | Path | None = None,
        stems: str | Path | None = None,
        archetypes: str | Path | None = None,
        model_dir: str | Path | None = None,
    ) -> AppConfig:
        base = Path(root or os.environ.get(_ENV_HOME) or _default_base()).resolve()

        def _path(override: str | Path | None, default: str) -> Path:
            return Path(override).resolve() if override else base / default

        return cls(
            root=base,
            db_path=_path(db, _DEFAULT_DB),
            library_root=_path(library, _DEFAULT_LIBRARY),
            stems_dir=_path(stems, _DEFAULT_STEMS),
            archetypes_path=_path(archetypes, _DEFAULT_ARCHETYPES),
            model_dir=Path(model_dir).resolve() if model_dir else None,
        )

    def ensure_dirs(self) -> None:
        """Create the writable directories this config points at.

        Called by entry points at startup, never at import: a frozen first run
        has no data directory at all, and `sqlite3.connect` will not create a
        missing parent.
        """
        for directory in (self.root, self.db_path.parent, self.library_root, self.stems_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if self.model_dir is not None:
            self.model_dir.mkdir(parents=True, exist_ok=True)

    def ensure_user_resources(self) -> None:
        """Seed user-editable copies of bundled defaults on first run.

        Never overwrites an existing file — the user's archetypes are their
        calibration and an update must not silently discard them. Same principle
        as `user_modified` on the presets table.
        """
        if self.archetypes_path.exists():
            return
        bundled = resource_path(_DEFAULT_ARCHETYPES)
        if bundled.exists() and bundled != self.archetypes_path:
            self.archetypes_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundled, self.archetypes_path)


def add_config_args(parser: argparse.ArgumentParser) -> None:
    """Register the shared resource-location flags on a CLI parser."""
    group = parser.add_argument_group("resource locations")
    group.add_argument("--root", default=None,
                       help=f"Base dir for db/music/stems/archetypes (default: ${_ENV_HOME} or CWD)")
    group.add_argument("--db", default=None, help="SQLite database path")
    group.add_argument("--library-root", default=None, help="Root folder where audio files live")
    group.add_argument("--stems-dir", default=None, help="Guitar stem cache directory")
    group.add_argument("--archetypes", default=None, help="Tone archetypes JSON path")
    group.add_argument("--model-dir", default=None, help="Separation model cache directory")


def config_from_args(args: argparse.Namespace) -> AppConfig:
    """Build an AppConfig from a namespace populated by add_config_args."""
    return AppConfig.resolve(
        getattr(args, "root", None),
        db=getattr(args, "db", None),
        library=getattr(args, "library_root", None),
        stems=getattr(args, "stems_dir", None),
        archetypes=getattr(args, "archetypes", None),
        model_dir=getattr(args, "model_dir", None),
    )
