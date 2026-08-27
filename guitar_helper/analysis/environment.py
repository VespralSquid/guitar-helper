from __future__ import annotations

import importlib.util
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .audio_loader import _SUPPORTED_NATIVE, _SUPPORTED_PYDUB
from .source_separator import default_model_dir

SUPPORTED_SUFFIXES: frozenset[str] = frozenset(_SUPPORTED_NATIVE | _SUPPORTED_PYDUB)
FFMPEG_SUFFIXES: frozenset[str] = frozenset(_SUPPORTED_PYDUB)

_SEPARATION_MODULES = ("audio_separator", "torch", "onnxruntime", "diffq")
_MODEL_CONFIG = "htdemucs_6s.yaml"
_MIN_WEIGHTS_BYTES = 10 * 1024 * 1024


class Severity(StrEnum):
    BLOCK = "block"   # ingestion cannot start
    WARN = "warn"     # ingestion can start; some formats or files will fail


@dataclass(frozen=True)
class Check:
    name: str          # stable id — tests assert on this
    ok: bool
    severity: Severity # the severity this check carries WHEN it fails
    detail: str        # what was found; for humans
    remedy: str        # what to do; for humans


@dataclass(frozen=True)
class FileCheck:
    path: Path
    code: str          # "ok" | "missing" | "unsupported_suffix" | "needs_ffmpeg" | "unreadable"
    detail: str

    @property
    def ok(self) -> bool:
        return self.code == "ok"


@dataclass(frozen=True)
class EnvironmentReport:
    """Preflight verdict. The separation-stack check is find_spec-based and is
    necessary but NOT sufficient — a --no-deps install can satisfy it and still
    fail to import. Use check_separation_stack(deep=True) off the main thread
    for the authoritative answer."""
    checks: list[Check] = field(default_factory=list)
    files: list[FileCheck] = field(default_factory=list)

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.severity is Severity.BLOCK]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.severity is Severity.WARN]

    @property
    def usable_files(self) -> list[Path]:
        return [f.path for f in self.files if f.ok]

    @property
    def rejected_files(self) -> list[FileCheck]:
        return [f for f in self.files if not f.ok]

    @property
    def can_proceed(self) -> bool:
        return not self.blockers and bool(self.usable_files)


def _is_writable(directory: str | Path) -> tuple[bool, str]:
    """Create directory if needed, then prove it is writable with an actual
    write. os.access is unreliable on Windows for this question."""
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=True):
            pass
        return True, ""
    except OSError as exc:
        return False, str(exc)


def check_separation_stack(*, deep: bool = False) -> Check:
    """Shallow: find_spec over the stack's fragile members — necessary but
    NOT sufficient, since a --no-deps install can satisfy find_spec while its
    import chain is broken. deep=True does a real `import audio_separator.
    separator`, costs ~2.8 s and permanently loads torch into the process —
    never call it on the Qt main thread."""
    missing = [m for m in _SEPARATION_MODULES if importlib.util.find_spec(m) is None]
    if missing:
        return Check(
            name="separation_stack",
            ok=False,
            severity=Severity.BLOCK,
            detail=f"Missing module(s): {', '.join(missing)}",
            remedy="Install the separation stack (see requirements-separation.txt).",
        )

    if deep:
        try:
            import audio_separator.separator  # noqa: F401, PLC0415
        except Exception as exc:  # noqa: BLE001
            return Check(
                name="separation_stack",
                ok=False,
                severity=Severity.BLOCK,
                detail=f"Modules present but import failed: {exc}",
                remedy="Reinstall the separation stack (see requirements-separation.txt).",
            )

    return Check(
        name="separation_stack", ok=True, severity=Severity.BLOCK,
        detail="Separation stack modules present.", remedy="",
    )


def check_model_weights(model_dir: str | Path | None = None) -> Check:
    resolved = Path(model_dir) if model_dir is not None else default_model_dir()
    config = resolved / _MODEL_CONFIG
    has_config = config.is_file()
    has_weights = has_config and any(
        p.is_file() and p.stat().st_size >= _MIN_WEIGHTS_BYTES for p in resolved.glob("*.th")
    )
    if has_config and has_weights:
        return Check(
            name="model_weights", ok=True, severity=Severity.BLOCK,
            detail=f"{_MODEL_CONFIG} and weights present under {resolved}.", remedy="",
        )
    return Check(
        name="model_weights",
        ok=False,
        severity=Severity.BLOCK,
        detail=f"{_MODEL_CONFIG} and/or weights missing under {resolved}.",
        remedy=(
            "Reinstall, or run one separation from the CLI with a network "
            "connection to re-fetch the htdemucs_6s weights."
        ),
    )


def check_ffmpeg() -> Check:
    """ffmpeg is a BLOCK, not a WARN.

    This reverses the earlier "ffmpeg is a WARN that becomes a per-file
    rejection" decision, which was made when a run could avoid separation.
    It cannot any more: build_pipeline always uses AudioSeparator, and
    audio_separator's Separator.__init__ calls check_ffmpeg_installed(), which
    raises FileNotFoundError when ffmpeg is absent. Without ffmpeg the
    separator cannot be constructed at all, so *no* file can be analysed --
    including .wav. The per-file `needs_ffmpeg` rejection still applies on top,
    to explain which files also need it merely to decode.
    """
    found = shutil.which("ffmpeg")
    if found:
        return Check(
            name="ffmpeg", ok=True, severity=Severity.BLOCK,
            detail=f"ffmpeg found at {found}.", remedy="",
        )
    return Check(
        name="ffmpeg",
        ok=False,
        severity=Severity.BLOCK,
        detail="ffmpeg not found on PATH.",
        remedy=(
            "Install ffmpeg and restart: winget install --id Gyan.FFmpeg -e. "
            "Guitar stem separation cannot run without it, so no song can be analysed."
        ),
    )


def check_writable(directory: str | Path, *, name: str) -> Check:
    ok, detail = _is_writable(directory)
    if ok:
        return Check(name=name, ok=True, severity=Severity.BLOCK, detail=f"{directory} is writable.", remedy="")
    return Check(
        name=name,
        ok=False,
        severity=Severity.BLOCK,
        detail=f"{directory} is not writable: {detail}",
        remedy="Choose a writable location, or fix permissions on this directory.",
    )


def check_files(paths: Sequence[str | Path], *, ffmpeg_available: bool) -> list[FileCheck]:
    results: list[FileCheck] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            results.append(FileCheck(path=path, code="missing", detail="File not found."))
            continue

        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            results.append(FileCheck(path=path, code="unsupported_suffix", detail=f"Unsupported format '{suffix}'."))
            continue

        if suffix in FFMPEG_SUFFIXES and not ffmpeg_available:
            results.append(FileCheck(path=path, code="needs_ffmpeg", detail="Requires ffmpeg, not found on PATH."))
            continue

        try:
            with open(path, "rb") as f:
                f.read(1)
        except OSError as exc:
            results.append(FileCheck(path=path, code="unreadable", detail=str(exc)))
            continue

        results.append(FileCheck(path=path, code="ok", detail=""))

    return results


def preflight(
    paths: Sequence[str | Path] = (),
    *,
    stems_dir: str | Path,
    db_path: str | Path,
    model_dir: str | Path | None = None,
) -> EnvironmentReport:
    """Run every check and wire the ffmpeg result into the per-file pass.
    Never hashes — duplicate/corrections classification is precheck()'s job,
    on the main thread, per file."""
    ffmpeg_check = check_ffmpeg()
    checks = [
        check_separation_stack(),
        check_model_weights(model_dir),
        ffmpeg_check,
        check_writable(stems_dir, name="stem_cache_writable"),
        check_writable(Path(db_path).parent, name="database_writable"),
    ]
    files = check_files(paths, ffmpeg_available=ffmpeg_check.ok)
    return EnvironmentReport(checks=checks, files=files)
