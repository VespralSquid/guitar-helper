# PyInstaller spec - onedir. See docs/new feature specs/Packaging_and_Update_Strategy.md
#
# onedir, not onefile: onefile self-extracts the whole bundle to a temp directory
# on every launch, which at this size costs seconds per start and defeats
# file-level patching. Keeping the Qt DLLs as separate files is also what makes
# the LGPL relink right meaningful.
#
# The separation tier (torch / onnxruntime / audio-separator / htdemucs_6s) IS
# bundled. Separation is mandatory - the classifier is calibrated on guitar-stem
# features, so a build without it cannot analyse anything (stem vs full-mix
# accuracy .821 vs .463). It roughly triples the download; that is the cost of
# shipping a working app.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

# htdemucs_6s weights. Not in git (55 MB); resolved at build time and REQUIRED -
# a bundle without them silently loses the only feature that matters.
_MODEL_DIR = Path(
    os.environ.get("GUITAR_HELPER_MODEL_DIR") or r"C:\tmp\audio-separator-models"
)
_MODEL_FILES = ["htdemucs_6s.yaml", "5c90dfd2-34c22ccb.th", "download_checks.json"]

missing = [f for f in _MODEL_FILES if not (_MODEL_DIR / f).is_file()]
if missing:
    raise SystemExit(
        f"htdemucs_6s model files missing from {_MODEL_DIR}: {', '.join(missing)}\n"
        f"Set GUITAR_HELPER_MODEL_DIR to the directory holding them, or run one "
        f"separation from the CLI with a network connection to fetch them."
    )

# librosa reaches for these lazily (inside functions, or by string), so the
# import graph does not see them and they must be named explicitly. Symptom of a
# missing one is always ModuleNotFoundError at runtime, never at build time.
hiddenimports = [
    "sklearn.utils._typedefs",
    "sklearn.utils._heap",
    "sklearn.utils._sorting",
    "sklearn.utils._vector_sentinel",
    "sklearn.neighbors._partition_nodes",
    "scipy.special.cython_special",
    "soxr",
    "lazy_loader",
    "audioop",
    "soundfile",
    "sounddevice",
    "mido.backends.rtmidi",
    # Only ever imported inside functions (guitar_helper/update). The update
    # check fails silently by design, so a missing requests would break the
    # whole update channel with nothing visible to the user.
    "requests",
    # Separation tier. audio-separator imports its model architectures by
    # string, and diffq is imported eagerly by its bundled Demucs even though
    # htdemucs_6s never calls it (see installer/stubs/diffq.py).
    "diffq",
    "audio_separator",
    "audio_separator.separator",
    "torch",
    "onnxruntime",
    "onnx2torch",
    "einops",
    "julius",
    "ml_collections",
    "rotary_embedding_torch",
    "samplerate",
    "resampy",
    "beartype",
    "pydub",
    "yaml",
    "tqdm",
]
hiddenimports += collect_submodules("librosa")
hiddenimports += collect_submodules("numba")
hiddenimports += collect_submodules("audio_separator")

datas = [
    (str(ROOT / "archetypes.json"), "."),
    # Help > User guide resolves this through resource_path, so it has to be
    # bundled at the same relative location it has in the checkout.
    (str(ROOT / "docs" / "user-guide.md"), "docs"),
    (str(ROOT / "LICENSE"), "."),
]
datas += [(str(_MODEL_DIR / f), "models") for f in _MODEL_FILES]

# librosa ships example audio and version metadata it reads at import time;
# lazy_loader needs the .pyi stubs that describe librosa's lazy namespace.
datas += collect_data_files("librosa", includes=["**/*.pyi", "**/version.py"])
datas += collect_data_files("soundfile")
datas += collect_data_files("audio_separator")
datas += collect_data_files("onnxruntime")
# torch's data is mostly headers and test fixtures; take only what it loads at
# runtime. collect_data_files("torch") unfiltered adds ~200 MB of .h files.
datas += collect_data_files("torch", includes=["**/*.json", "**/version.py", "**/*.yaml"])

binaries = collect_dynamic_libs("torch") + collect_dynamic_libs("onnxruntime")

# Qt is the single biggest size lever after torch. The app uses
# Core/Gui/Widgets only, and the default collection would otherwise pull
# WebEngine, Quick, 3D, Charts, Multimedia and every translation.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtWebSockets", "PySide6.QtWebChannel",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    # torch fat that never runs here. Verified by checking sys.modules after a
    # bare `import torch`: everything listed is absent from it.
    #
    # torch.distributed and torch.testing are NOT excludable - torch/__init__.py
    # imports both unconditionally, so excluding them breaks `import torch`
    # outright and takes audio-separator down with it. That was a real build
    # failure, caught by --selftest; do not "optimise" them back out.
    "torch.utils.tensorboard", "torch.utils.benchmark",
    "torch._inductor", "torch._dynamo", "torch.include",
    "torchaudio", "torchvision",
    # Dev-only.
    "pytest", "ruff", "IPython", "matplotlib", "tkinter", "pyqtgraph",
]

a = Analysis(
    [str(ROOT / "installer" / "launcher.py")],
    # installer/stubs supplies the vendored diffq, so the build does not depend
    # on the stub having been dropped into the venv by hand.
    pathex=[str(ROOT), str(ROOT / "installer" / "stubs")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "installer" / "runtime_hook.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GuitarHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GuitarHelper",
)
