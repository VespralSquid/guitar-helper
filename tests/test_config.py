from __future__ import annotations

import argparse
from pathlib import Path

from guitar_helper.config import AppConfig, add_config_args, config_from_args


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
