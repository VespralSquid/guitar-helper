"""Entry point for the frozen build.

A windowed PyInstaller build differs from `python -m guitar_helper.run_ui` in
three ways that all have to be handled before anything else imports:

  * `sys.stdout` / `sys.stderr` are None. Any library that prints — and
    audio-separator's tqdm and INFO logging both do — raises AttributeError on
    `None.write`. They are replaced with a log file.
  * There is no console to show a traceback in, so an unhandled exception would
    close the window with no explanation.
  * `multiprocessing` needs `freeze_support()` before a child process is
    spawned, or the child re-runs this script and forks endlessly.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import traceback
from pathlib import Path


def _log_path() -> Path:
    from guitar_helper.config import AppConfig  # noqa: PLC0415 — after stream setup

    root = Path(os.environ.get("GUITAR_HELPER_HOME") or AppConfig.resolve().root)
    root.mkdir(parents=True, exist_ok=True)
    return root / "guitar-helper.log"


def _redirect_streams() -> None:
    """Point the null stdout/stderr of a windowed build at a log file.

    Best-effort: if the log cannot be opened the app still has to start, so the
    streams fall back to a sink that swallows writes rather than raising.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return

    stream = None
    try:
        stream = open(_log_path(), "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except Exception:  # noqa: BLE001
        stream = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _report_fatal(exc: BaseException) -> None:
    """Show a dialog for an exception that would otherwise close the window
    silently. Falls back to the log if Qt itself is what failed."""
    detail = "".join(traceback.format_exception(exc))
    print(detail, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: PLC0415

        app = QApplication.instance() or QApplication([])
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Guitar Helper")
        box.setText("Guitar Helper could not start.")
        box.setInformativeText(str(exc) or exc.__class__.__name__)
        box.setDetailedText(detail)
        box.exec()
        del app
    except Exception:  # noqa: BLE001 — the log already has the real traceback
        pass


_SELFTEST_IMPORTS = [
    "librosa",
    "numba",
    "sklearn.neighbors",
    "scipy.signal",
    "soundfile",
    "sounddevice",
    "mido",
    "mutagen",
    "requests",
    "guitar_helper.update",
    "torch",
    "onnxruntime",
    "diffq",
    "audio_separator.separator",
    "numpy",
    "PySide6.QtWidgets",
    "guitar_helper.analysis.pipeline",
    "guitar_helper.analysis.feature_extractor",
    "guitar_helper.analysis.segmenter",
    "guitar_helper.playback.playback_engine",
    "guitar_helper.ui.main_window",
]


def _selftest() -> int:
    """Import everything the UI defers until a track is analysed.

    A frozen build that launches proves very little: librosa, numba and sklearn
    are not touched until the first analysis, so a missing hidden import stays
    invisible until a user tries to add a song. This forces all of it up front.
    """
    import importlib  # noqa: PLC0415

    failures = []
    for name in _SELFTEST_IMPORTS:
        try:
            importlib.import_module(name)
            print(f"ok    {name}")
        except Exception as exc:  # noqa: BLE001
            failures.append((name, exc))
            print(f"FAIL  {name}: {exc.__class__.__name__}: {exc}")

    from guitar_helper.config import AppConfig, is_frozen, resource_path  # noqa: PLC0415

    cfg = AppConfig.resolve()
    print(f"frozen={is_frozen()} root={cfg.root}")
    print(f"NUMBA_CACHE_DIR={os.environ.get('NUMBA_CACHE_DIR')}")
    bundled = resource_path("archetypes.json")
    print(f"bundled archetypes: {bundled} exists={bundled.exists()}")

    # GPLv3: the licence, the copyright/source notice and the third-party
    # notices must accompany the binary, and the About dialog links all three.
    # A build that silently drops them is a compliance failure, not cosmetic.
    for name in ("LICENSE", "COPYRIGHT", "THIRD-PARTY-NOTICES.md"):
        path = resource_path(name)
        if path.is_file():
            print(f"ok    legal: {name}")
        else:
            print(f"FAIL  legal: {name} missing from the bundle ({path})")
            failures.append((f"legal:{name}", None))

    if not _selftest_extract():
        failures.append(("feature extraction", None))

    if not _selftest_separation():
        failures.append(("separation readiness", None))

    ok = len(_SELFTEST_IMPORTS) - len([f for f in failures if f[1] is not None])
    print(f"\n{ok}/{len(_SELFTEST_IMPORTS)} imports ok")
    return 1 if failures else 0


def _selftest_extract() -> bool:
    """Run the numba/scipy hot path for real.

    Importing librosa proves nothing about numba: it JIT-compiles on first call
    and writes its cache next to the compiled module, which in a bundle is the
    read-only install directory. This is what the runtime hook exists to fix, so
    it needs an actual extraction to verify.
    """
    try:
        import numpy as np  # noqa: PLC0415

        from guitar_helper.analysis.feature_extractor import FeatureExtractor  # noqa: PLC0415

        sr = 22050
        t = np.linspace(0, 3.0, sr * 3, endpoint=False, dtype=np.float32)
        y = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

        matrix = FeatureExtractor(hop_length=512).extract(y, sr)
        print(f"ok    feature extraction -> {matrix.shape}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  feature extraction: {exc.__class__.__name__}: {exc}")
        print(traceback.format_exc())
        return False
    return True


def _selftest_separation() -> bool:
    """Prove the bundled separation tier is actually usable.

    Importing torch says nothing about whether the htdemucs_6s weights shipped
    or whether audio-separator can find them. Separation is mandatory - the
    classifier is calibrated on stem features - so a bundle that imports fine
    but cannot separate is a broken product, not a degraded one.

    A real separation takes minutes, so this checks everything up to the run:
    the weights are present where the separator will look, and Separator
    constructs (which is also where its ffmpeg check fires).
    """
    ok = True
    try:
        from guitar_helper.analysis.environment import (  # noqa: PLC0415
            check_ffmpeg,
            check_model_weights,
            check_separation_stack,
        )
        from guitar_helper.analysis.source_separator import default_model_dir  # noqa: PLC0415

        model_dir = default_model_dir()
        print(f"model dir: {model_dir}")

        for check in (check_separation_stack(deep=True), check_model_weights(), check_ffmpeg()):
            mark = "ok   " if check.ok else "FAIL "
            print(f"{mark} {check.name}: {check.detail}")
            if not check.ok:
                # ffmpeg is an end-user prerequisite, not a build defect, so it
                # must not fail the build's own self-test.
                if check.name == "ffmpeg":
                    print(f"      (prerequisite, not bundled) {check.remedy}")
                    continue
                ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  separation readiness: {exc.__class__.__name__}: {exc}")
        print(traceback.format_exc())
        return False
    return ok


def main() -> int:
    multiprocessing.freeze_support()
    _redirect_streams()
    try:
        if "--selftest" in sys.argv[1:]:
            return _selftest()

        from guitar_helper.run_ui import main as run_ui_main  # noqa: PLC0415

        run_ui_main()
    except SystemExit as exit_:
        return int(exit_.code or 0)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001
        # Last resort: without this the window closes with no explanation and
        # no console to have shown the traceback in.
        _report_fatal(exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
