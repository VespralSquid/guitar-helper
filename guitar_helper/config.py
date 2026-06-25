"""Single source of truth for resource locations.

Every path the app needs (database, audio library, stem cache, archetypes) is
derived from one `root`, so nothing depends on the process's current working
directory. `root` precedence: explicit argument > GUITAR_HELPER_HOME env var > CWD.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

_ENV_HOME = "GUITAR_HELPER_HOME"

_DEFAULT_DB = "library.db"
_DEFAULT_LIBRARY = "music"
_DEFAULT_STEMS = "stems"
_DEFAULT_ARCHETYPES = "archetypes.json"


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
        base = Path(root or os.environ.get(_ENV_HOME) or Path.cwd()).resolve()

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
