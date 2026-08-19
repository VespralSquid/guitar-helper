from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import soundfile as sf

_GUITAR_STEM = "Guitar"
_MODEL = "htdemucs_6s.yaml"
_CACHE_FORMAT_VERSION = 1
_DEFAULT_MODEL_DIR = "/tmp/audio-separator-models/"  # matches audio_separator.Separator.__init__
_MODEL_DIR_ENV = "AUDIO_SEPARATOR_MODEL_DIR"
_WEIGHT_SUFFIXES = frozenset({".th", ".onnx", ".ckpt", ".pt", ".pth"})
_DIGEST_WINDOW = 1 << 20
_DURATION_TOLERANCE_MS = 250
_STALE_TMP_AGE_S = 6 * 3600
_MIN_FREE_BYTES = 200 * 1024**2
_WAV_HEADER_BYTES = 44
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000

_REQUIRED_MANIFEST_KEYS = (
    "cache_format_version",
    "source_hash",
    "stem_bytes",
    "stem_frames",
    "stem_samplerate",
    "stem_sample_digest",
    "model",
    "model_files",
    "source_duration_ms",
)


class ISourceSeparator(ABC):

    @abstractmethod
    def separate_guitar(self, path: Path, file_hash: str) -> Path:
        """Return a path to the guitar-stem audio for the given track."""


class NullSeparator(ISourceSeparator):
    """Passthrough — returns the input unchanged. TEST AND FIXTURE USE ONLY: analysing a full
    mix scores 0.463 accuracy against 0.821 for a separated stem (ISSUE-007 §2.4). Never
    construct this for a library the user will play from.
    """

    def separate_guitar(self, path: Path, file_hash: str) -> Path:
        return Path(path)


class SeparationError(RuntimeError):
    """Separation did not produce a usable, publishable stem."""


def _sample_digest(path: Path, size: int) -> tuple[str, bytes]:
    """Return (digest string, concatenated sampled bytes).

    Windows at sorted({0, (size - W)//2, size - W}) clamped to >= 0, deduped, W = 1 MiB.
    Digest preimage is str(size).encode() then each window's bytes in offset order.
    Returns the raw bytes too, so callers can test them for all-zero without a second read.
    """
    window = min(_DIGEST_WINDOW, size)
    offsets = sorted({max(0, offset) for offset in (0, (size - window) // 2, size - window)})
    sampled = bytearray()
    with path.open("rb") as f:
        for offset in offsets:
            f.seek(offset)
            sampled += f.read(window)
    digest = hashlib.sha256()
    digest.update(str(size).encode())
    digest.update(sampled)
    return f"sha256-3x1M:{digest.hexdigest()}", bytes(sampled)


def _stem_duration_ms(info) -> int:
    return int(info.frames / info.samplerate * 1000)


def _replace_with_retry(src: Path, dest: Path, attempts: int = 3, delay: float = 0.25) -> None:
    """os.replace is atomic but not unconditionally-succeeding on Windows: it raises
    PermissionError if the destination is open by anyone (measured, see lane-b-design F3).
    """
    last_exc: OSError | None = None
    for attempt in range(attempts):
        try:
            os.replace(src, dest)
            return
        except OSError as exc:
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


class AudioSeparator(ISourceSeparator):
    """Isolate the guitar stem via python-audio-separator (htdemucs_6s).

    The separated stem is cached at cache_dir/<file_hash>_guitar.wav alongside a
    cache_dir/<file_hash>_guitar.json completion manifest; re-analysis of the same file
    reuses the cache and skips the costly separation pass, but only if the manifest
    validates the cached wav (see _check_cache) — existence alone is not trusted.
    """

    def __init__(
        self,
        cache_dir: str | Path = "stems",
        model_dir: str | Path | None = None,
        verbose: bool = False,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._model_dir = str(model_dir) if model_dir is not None else None
        self._verbose = verbose
        self._model_identity_cache: tuple[str, list[dict] | None] | None = None
        self._swept = False

    def separate_guitar(self, path: Path, file_hash: str) -> Path:
        path = Path(path)
        cached = self._cache_dir / f"{file_hash}_guitar.wav"
        manifest_path = self._manifest_path(file_hash)

        reason = self._check_cache(cached, path, file_hash)
        if reason is None:
            if self._verbose:
                print(f"  [separator] cache hit {cached}")
            return cached
        if self._verbose:
            print(f"  [separator] cache miss ({reason}); separating {path.name} (this may take minutes)")

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._perform_separation(path, file_hash, cached, manifest_path)

    # -- cache validation -----------------------------------------------------------

    def _manifest_path(self, file_hash: str) -> Path:
        return self._cache_dir / f"{file_hash}_guitar.json"

    def _model_identity(self) -> tuple[str, list[dict] | None]:
        if self._model_identity_cache is None:
            self._model_identity_cache = self._derive_model_identity()
        return self._model_identity_cache

    def _resolve_model_dir(self) -> Path:
        if self._model_dir is not None:
            return Path(self._model_dir)
        env = os.environ.get(_MODEL_DIR_ENV)
        if env:
            return Path(env)
        return Path(_DEFAULT_MODEL_DIR)

    def _derive_model_identity(self) -> tuple[str, list[dict] | None]:
        model_dir = self._resolve_model_dir()
        try:
            text = (model_dir / _MODEL).read_text()
        except OSError:
            return _MODEL, None

        signatures = re.findall(r"[0-9a-f]{6,}", text)
        files: dict[str, int] = {}
        for signature in signatures:
            for candidate in model_dir.glob(f"{signature}*"):
                if candidate.suffix not in _WEIGHT_SUFFIXES:
                    continue
                try:
                    files[candidate.name] = candidate.stat().st_size
                except OSError:
                    continue
        if not files:
            return _MODEL, None
        model_files = [{"name": name, "bytes": size} for name, size in sorted(files.items())]
        return _MODEL, model_files

    def _warn_if_dehydrated(self, st: os.stat_result) -> None:
        attrs = getattr(st, "st_file_attributes", 0)
        if attrs & _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS and self._verbose:
            print("  [separator] warning: cached stem appears dehydrated (OneDrive Files On-Demand)")

    def _check_cache(self, cached: Path, source: Path, file_hash: str) -> str | None:
        if not cached.exists():
            return "no-wav"
        st = cached.stat()
        if st.st_size == 0:
            return "empty-wav"
        self._warn_if_dehydrated(st)

        manifest_path = self._manifest_path(file_hash)
        if not manifest_path.exists():
            return self._try_grandfather(cached, source, file_hash)

        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            return "manifest-unreadable"
        if not isinstance(manifest, dict) or any(key not in manifest for key in _REQUIRED_MANIFEST_KEYS):
            return "manifest-unreadable"

        if manifest["cache_format_version"] != _CACHE_FORMAT_VERSION:
            return "format-version"
        if manifest["source_hash"] != file_hash:
            return "source-hash"
        if manifest["stem_bytes"] != st.st_size:
            return "stem-bytes"

        cur_model, cur_model_files = self._model_identity()
        if cur_model_files is not None and manifest["model_files"] is not None:
            if (cur_model, cur_model_files) != (manifest["model"], manifest["model_files"]):
                return "model"

        try:
            info = sf.info(cached)
        except Exception:
            return "stem-unreadable"
        if info.frames != manifest["stem_frames"] or info.samplerate != manifest["stem_samplerate"]:
            return "stem-shape"

        if manifest["source_duration_ms"] is not None:
            if abs(_stem_duration_ms(info) - manifest["source_duration_ms"]) > _DURATION_TOLERANCE_MS:
                return "duration"

        try:
            digest, _sampled = _sample_digest(cached, st.st_size)
        except OSError:
            return "stem-unreadable"
        if digest != manifest["stem_sample_digest"]:
            return "content"

        return None

    def _try_grandfather(self, cached: Path, source: Path, file_hash: str) -> str | None:
        src_ms = self._source_duration_ms(source)
        if src_ms is None:
            return "no-source-duration"
        try:
            info = sf.info(cached)
        except Exception:
            return "stem-unreadable"
        if abs(_stem_duration_ms(info) - src_ms) > _DURATION_TOLERANCE_MS:
            return "duration"

        st = cached.stat()
        try:
            _digest, sampled = _sample_digest(cached, st.st_size)
        except OSError:
            return "stem-unreadable"
        if all(b == 0 for b in sampled[_WAV_HEADER_BYTES:]):
            return "all-zero"

        manifest = self._build_manifest(cached, file_hash, src_ms, grandfathered=True)
        try:
            self._publish_json(manifest, self._manifest_path(file_hash))
        except OSError as exc:
            # Same non-fatal treatment as P11: the stem itself already passed every
            # validation check above, so a manifest-write failure must not turn a
            # good stem into an error. The next hit retries the write.
            if self._verbose:
                print(f"  [separator] warning: grandfather manifest write failed: {exc}")
        return None

    def _source_duration_ms(self, source: Path) -> int | None:
        try:
            info = sf.info(source)
        except Exception:
            return None
        return _stem_duration_ms(info)

    def _build_manifest(
        self,
        stem: Path,
        file_hash: str,
        source_duration_ms: int | None,
        *,
        grandfathered: bool,
    ) -> dict:
        st = stem.stat()
        info = sf.info(stem)
        digest, _sampled = _sample_digest(stem, st.st_size)
        model, model_files = self._model_identity()
        try:
            separator_version = importlib.metadata.version("audio-separator")
        except importlib.metadata.PackageNotFoundError:
            separator_version = None
        return {
            "cache_format_version": _CACHE_FORMAT_VERSION,
            "source_hash": file_hash,
            "source_duration_ms": source_duration_ms,
            "stem_bytes": st.st_size,
            "stem_frames": info.frames,
            "stem_samplerate": info.samplerate,
            "stem_sample_digest": digest,
            "model": model,
            "model_files": model_files,
            "separator_version": separator_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "grandfathered": grandfathered,
        }

    def _publish_json(self, manifest: dict, dest: Path) -> None:
        tmp = dest.parent / f".mf-{uuid4().hex}.json"
        tmp.write_text(json.dumps(manifest, indent=2))
        fd = os.open(tmp, os.O_RDWR)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _replace_with_retry(tmp, dest)

    # -- separation + atomic publish -------------------------------------------------

    def _sweep_stale_tmp(self) -> None:
        if self._swept:
            return
        self._swept = True
        now = time.time()
        for entry in self._cache_dir.glob(".tmp-*"):
            try:
                stale = now - entry.stat().st_mtime >= _STALE_TMP_AGE_S
            except OSError:
                continue
            if stale:
                shutil.rmtree(entry, ignore_errors=True)

    def _perform_separation(self, path: Path, file_hash: str, cached: Path, manifest_path: Path) -> Path:
        source_bytes = path.stat().st_size
        free = shutil.disk_usage(self._cache_dir).free
        needed = max(2 * source_bytes, _MIN_FREE_BYTES)
        if free < needed:
            raise SeparationError(
                f"insufficient disk space in {self._cache_dir}: {free} bytes free, need at least {needed}"
            )

        self._sweep_stale_tmp()

        tmp = self._cache_dir / f".tmp-{file_hash[:8]}-{os.getpid()}-{uuid4().hex[:8]}"
        tmp.mkdir(parents=True)
        try:
            # Lazy import: keeps torch/onnxruntime out of the import path for tests and
            # for runs that use NullSeparator.
            from audio_separator.separator import Separator

            kwargs = {
                "output_dir": str(tmp),
                "output_format": "WAV",
                "output_single_stem": _GUITAR_STEM,
            }
            if self._model_dir is not None:
                kwargs["model_file_dir"] = self._model_dir
            separator = Separator(**kwargs)
            separator.load_model(model_filename=_MODEL)
            produced_names = separator.separate(str(path), {_GUITAR_STEM: f"{file_hash}_guitar"})

            produced = tmp / f"{file_hash}_guitar.wav"
            if not produced.exists():
                wavs = sorted(tmp.glob("*.wav"))
                if len(wavs) == 1:
                    produced = wavs[0]
                else:
                    raise SeparationError(f"separator produced no usable output: {produced_names}")

            try:
                info = sf.info(produced)
            except Exception as exc:
                raise SeparationError(f"produced stem is unreadable: {exc}") from exc

            src_ms = self._source_duration_ms(path)
            if src_ms is not None and abs(_stem_duration_ms(info) - src_ms) > _DURATION_TOLERANCE_MS:
                raise SeparationError(
                    f"produced stem duration {_stem_duration_ms(info)} ms does not match "
                    f"source duration {src_ms} ms"
                )

            fd = os.open(produced, os.O_RDWR)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            # No directory fsync: raises PermissionError on Windows (F3).

            manifest = self._build_manifest(produced, file_hash, src_ms, grandfathered=False)

            manifest_path.unlink(missing_ok=True)

            try:
                _replace_with_retry(produced, cached)
            except OSError as exc:
                if self._check_cache(cached, path, file_hash) is None:
                    return cached
                raise SeparationError(f"failed to publish separated stem: {exc}") from exc

            try:
                self._publish_json(manifest, manifest_path)
            except OSError as exc:
                if self._verbose:
                    print(f"  [separator] warning: manifest publish failed: {exc}")

            return cached
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
