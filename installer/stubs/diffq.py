"""Minimal stub for `diffq`.

The real diffq / diffq-fixed packages are Cython extensions with no Python 3.14
wheel and a broken sdist, so they cannot be installed here. audio-separator's
bundled Demucs eagerly imports these three names at module load
(see uvr_lib_v5/demucs/states.py), but they are only *invoked* for quantized
models. htdemucs_6s is not quantized, so these placeholders are never called.

If a quantized model is ever loaded, these raise instead of failing silently.
"""

_MSG = (
    "diffq is a stub (no Python 3.14 build available). Quantized Demucs models "
    "are unsupported in this environment; htdemucs_6s does not need diffq."
)


class DiffQuantizer:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(_MSG)


class UniformQuantizer:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(_MSG)


def restore_quantized_state(*args, **kwargs):
    raise NotImplementedError(_MSG)


# Vendored here, not just in the venv: the frozen build must be reproducible on
# a fresh checkout. installer/stubs is added to the spec's pathex so PyInstaller
# finds this copy even when the build venv has no diffq installed.
