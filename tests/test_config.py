from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

import guitar_helper.config as config_mod
from guitar_helper.config import (
    AppConfig,
    add_config_args,
    config_from_args,
    is_frozen,
    resource_path,
    user_data_dir,
)


# Test 1 — defaults derive every path from the given root
def test_resolve_derives_paths_from_root(tmp_path):
    cfg = AppConfig.resolve(tmp_path)
    assert cfg.root == tmp_path.resolve()
    assert cfg.db_path == tmp_path.resolve() / "library.db"
    assert cfg.library_root == tmp_path.resolve() / "music"
    assert cfg.stems_dir == tmp_path.resolve() / "stems"
    assert cfg.archetypes_path == tmp_path.resolve() / "archetypes.json"
    assert cfg.model_dir is None


# Test 2 — explicit overrides win over root-derived defaults
def test_resolve_explicit_overrides(tmp_path):
    cfg = AppConfig.resolve(
        tmp_path,
        db=tmp_path / "custom.db",
        stems=tmp_path / "s",
        archetypes=tmp_path / "arch.json",
        model_dir=tmp_path / "models",
    )
    assert cfg.db_path == (tmp_path / "custom.db").resolve()
    assert cfg.stems_dir == (tmp_path / "s").resolve()
    assert cfg.archetypes_path == (tmp_path / "arch.json").resolve()
    assert cfg.model_dir == (tmp_path / "models").resolve()
    assert cfg.library_root == tmp_path.resolve() / "music"  # untouched default


# Test 3 — GUITAR_HELPER_HOME is used when no explicit root
def test_resolve_env_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GUITAR_HELPER_HOME", str(tmp_path))
    cfg = AppConfig.resolve()
    assert cfg.root == tmp_path.resolve()
    assert cfg.db_path == tmp_path.resolve() / "library.db"


# Test 4 — explicit root beats the env var
def test_resolve_explicit_root_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GUITAR_HELPER_HOME", str(tmp_path / "env"))
    cfg = AppConfig.resolve(tmp_path / "explicit")
    assert cfg.root == (tmp_path / "explicit").resolve()


# Test 5 — falls back to CWD when neither root nor env set
def test_resolve_cwd_fallback(monkeypatch):
    monkeypatch.delenv("GUITAR_HELPER_HOME", raising=False)
    cfg = AppConfig.resolve()
    assert cfg.root == Path.cwd().resolve()


# Test 6 — add_config_args + config_from_args round-trip
def test_config_from_args(tmp_path):
    parser = argparse.ArgumentParser()
    add_config_args(parser)
    args = parser.parse_args(["--root", str(tmp_path), "--db", str(tmp_path / "x.db")])
    cfg = config_from_args(args)
    assert cfg.root == tmp_path.resolve()
    assert cfg.db_path == (tmp_path / "x.db").resolve()
    assert cfg.stems_dir == tmp_path.resolve() / "stems"


# --- frozen-build path resolution -------------------------------------------


def _freeze(monkeypatch, data_root: Path, meipass: Path | None = None) -> None:
    """Simulate a PyInstaller bundle. `sys.frozen` is absent in a checkout, so
    it must be raised with raising=False and deleted again by monkeypatch."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(config_mod, "user_data_dir", lambda: data_root)
    if meipass is not None:
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)


# Test 7 — a frozen build defaults to the per-user data dir, not the CWD
def test_frozen_defaults_to_user_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GUITAR_HELPER_HOME", raising=False)
    data_root = tmp_path / "LocalAppData" / "GuitarHelper"
    _freeze(monkeypatch, data_root)

    cfg = AppConfig.resolve()
    assert cfg.root == data_root.resolve()
    assert cfg.db_path == data_root.resolve() / "library.db"
    assert cfg.stems_dir == data_root.resolve() / "stems"
    assert cfg.root != Path.cwd().resolve()


# Test 8 — the precedence chain still wins over the frozen default
def test_frozen_explicit_root_and_env_still_win(tmp_path, monkeypatch):
    data_root = tmp_path / "LocalAppData" / "GuitarHelper"
    _freeze(monkeypatch, data_root)

    monkeypatch.setenv("GUITAR_HELPER_HOME", str(tmp_path / "env"))
    assert AppConfig.resolve().root == (tmp_path / "env").resolve()
    assert AppConfig.resolve(tmp_path / "explicit").root == (tmp_path / "explicit").resolve()


# Test 9 — is_frozen reflects sys.frozen
def test_is_frozen(monkeypatch):
    assert is_frozen() is False
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert is_frozen() is True


# Test 10 — resource_path resolves to the repo root from a checkout
def test_resource_path_from_checkout():
    assert resource_path("archetypes.json") == (
        Path(config_mod.__file__).resolve().parent.parent / "archetypes.json"
    )


# Test 11 — resource_path follows sys._MEIPASS inside a bundle
def test_resource_path_from_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resource_path("archetypes.json") == tmp_path / "archetypes.json"


# Test 12 — resources are NOT reachable through --root
def test_resource_path_ignores_root(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    AppConfig.resolve(tmp_path / "elsewhere")
    assert resource_path("archetypes.json").parent == bundle


# Test 13 — user_data_dir lands under LOCALAPPDATA on Windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only path convention")
def test_user_data_dir_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert user_data_dir() == tmp_path / "GuitarHelper"


# Test 14 — ensure_dirs creates every writable location, including a first run
def test_ensure_dirs_creates_tree(tmp_path):
    root = tmp_path / "never" / "existed"
    cfg = AppConfig.resolve(root, model_dir=root / "models")
    cfg.ensure_dirs()
    for path in (cfg.root, cfg.library_root, cfg.stems_dir, cfg.model_dir):
        assert path.is_dir()


# Test 15 — ensure_dirs is idempotent
def test_ensure_dirs_idempotent(tmp_path):
    cfg = AppConfig.resolve(tmp_path)
    cfg.ensure_dirs()
    cfg.ensure_dirs()
    assert cfg.stems_dir.is_dir()


# Test 16 — first run copies the bundled archetypes into the data dir
def test_ensure_user_resources_seeds_on_first_run(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "archetypes.json").write_text(json.dumps({"clean": [1, 2, 3]}))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    cfg = AppConfig.resolve(tmp_path / "data")
    cfg.ensure_dirs()
    cfg.ensure_user_resources()

    assert json.loads(cfg.archetypes_path.read_text()) == {"clean": [1, 2, 3]}


# Test 17 — an existing archetypes file is never overwritten by an update
def test_ensure_user_resources_preserves_user_edits(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "archetypes.json").write_text(json.dumps({"clean": [1, 2, 3]}))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    cfg = AppConfig.resolve(tmp_path / "data")
    cfg.ensure_dirs()
    cfg.archetypes_path.write_text(json.dumps({"clean": [9, 9, 9]}))
    cfg.ensure_user_resources()

    assert json.loads(cfg.archetypes_path.read_text()) == {"clean": [9, 9, 9]}


# Test 18 — seeding is a no-op when source and destination are the same file
def test_ensure_user_resources_same_path_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    cfg = AppConfig.resolve(tmp_path)
    cfg.ensure_dirs()
    cfg.ensure_user_resources()
    assert not cfg.archetypes_path.exists()
