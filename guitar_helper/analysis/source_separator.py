from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

_GUITAR_STEM = "Guitar"
_MODEL = "htdemucs_6s.yaml"


class ISourceSeparator(ABC):

    @abstractmethod
    def separate_guitar(self, path: Path, file_hash: str) -> Path:
        """Return a path to the guitar-stem audio for the given track."""


class NullSeparator(ISourceSeparator):
    """Passthrough — returns the input unchanged. Used in tests and when separation is disabled."""

    def separate_guitar(self, path: Path, file_hash: str) -> Path:
        return Path(path)


class AudioSeparator(ISourceSeparator):
    """Isolate the guitar stem via python-audio-separator (htdemucs_6s).

    The separated stem is cached at cache_dir/<file_hash>_guitar.wav; re-analysis
    of the same file reuses the cache and skips the costly separation pass.
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

    def separate_guitar(self, path: Path, file_hash: str) -> Path:
        path = Path(path)
        cached = self._cache_dir / f"{file_hash}_guitar.wav"
        if cached.exists():
            if self._verbose:
                print(f"  [separator] cache hit {cached}")
            return cached

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        if self._verbose:
            print(f"  [separator] separating guitar stem from {path.name} (this may take minutes)")

        # Lazy import: keeps torch/onnxruntime out of the import path for tests and
        # for runs that use NullSeparator.
        from audio_separator.separator import Separator

        kwargs = {
            "output_dir": str(self._cache_dir),
            "output_format": "WAV",
            "output_single_stem": _GUITAR_STEM,
        }
        if self._model_dir is not None:
            kwargs["model_file_dir"] = self._model_dir
        separator = Separator(**kwargs)
        separator.load_model(model_filename=_MODEL)
        separator.separate(str(path), {_GUITAR_STEM: f"{file_hash}_guitar"})
        return cached
