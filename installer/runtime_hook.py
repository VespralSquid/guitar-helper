"""Runs inside the bundle before the entry script imports anything.

numba writes its JIT cache next to the module it compiled. In a frozen build
that is the install directory, which is read-only for a standard user, so the
cache has to be redirected before numba (via librosa) is first imported.
"""
import os
import sys


def _redirect_numba_cache() -> None:
    if os.environ.get("NUMBA_CACHE_DIR"):
        return
    try:
        from guitar_helper.config import user_data_dir

        cache = user_data_dir() / "cache" / "numba"
        cache.mkdir(parents=True, exist_ok=True)
        os.environ["NUMBA_CACHE_DIR"] = str(cache)
    except Exception:
        # An unwritable cache dir makes numba fall back to compiling every
        # time — slow, but not a reason to refuse to start.
        os.environ["NUMBA_DISABLE_JIT_CACHE"] = "1"


if getattr(sys, "frozen", False):
    _redirect_numba_cache()
