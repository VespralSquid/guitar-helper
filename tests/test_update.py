from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from guitar_helper.update import (
    UpdateError,
    UpdateManifest,
    UpdateVerificationError,
    is_newer,
    parse_manifest,
    parse_version,
)
from guitar_helper.update import manifest as manifest_mod
from guitar_helper.update import service as service_mod

GOOD_DIGEST = "a" * 64


def _doc(**overrides):
    base = {
        "version": "1.0.1",
        "url": "https://example.invalid/GuitarHelper-Setup-1.0.1.exe",
        "sha256": GOOD_DIGEST,
        "notes": "Fixes things.",
    }
    base.update(overrides)
    return base


# --- version comparison ------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("1.0.1", "1.0.0", True),
        ("1.1.0", "1.0.9", True),
        ("2.0.0", "1.99.99", True),
        ("1.0.0", "1.0.0", False),
        ("1.0.0", "1.0.1", False),
        ("1.9.0", "1.10.0", False),  # not a string compare
    ],
)
def test_is_newer(candidate, current, expected):
    assert is_newer(candidate, current) is expected


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0-beta", "", "abc", "1.0.0.0"])
def test_parse_version_rejects_malformed(bad):
    with pytest.raises(UpdateError):
        parse_version(bad)


# --- manifest validation -----------------------------------------------------


def test_parse_manifest_happy_path():
    m = parse_manifest(_doc())
    assert m.version == "1.0.1"
    assert m.sha256 == GOOD_DIGEST
    assert m.is_newer_than("1.0.0")


def test_parse_manifest_rejects_non_object():
    with pytest.raises(UpdateError):
        parse_manifest(["1.0.1"])


@pytest.mark.parametrize("field", ["version", "url", "sha256"])
def test_parse_manifest_requires_fields(field):
    doc = _doc()
    del doc[field]
    with pytest.raises(UpdateError) as excinfo:
        parse_manifest(doc)
    assert field in str(excinfo.value)


def test_parse_manifest_rejects_plain_http():
    # Downgrading the channel to http would let anyone on the path swap both
    # the installer and the digest that is supposed to authenticate it.
    with pytest.raises(UpdateError) as excinfo:
        parse_manifest(_doc(url="http://example.invalid/setup.exe"))
    assert "https" in str(excinfo.value)


@pytest.mark.parametrize("digest", ["abc", "z" * 64, "A" * 63, ""])
def test_parse_manifest_rejects_bad_digest(digest):
    with pytest.raises(UpdateError):
        parse_manifest(_doc(sha256=digest))


def test_parse_manifest_uppercases_digest_to_lower():
    assert parse_manifest(_doc(sha256="A" * 64)).sha256 == "a" * 64


def test_min_upgradable_from_gate():
    m = parse_manifest(_doc(min_upgradable_from="1.0.0"))
    assert m.upgradable_from("1.0.0") is True
    assert m.upgradable_from("1.2.0") is True
    assert m.upgradable_from("0.9.0") is False


def test_min_upgradable_from_absent_allows_any():
    assert parse_manifest(_doc()).upgradable_from("0.0.1") is True


# --- fetching ----------------------------------------------------------------


class _FakeRaw:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, size, decode_content=True):  # noqa: ARG002
        return self._payload[:size]


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200):
        self.raw = _FakeRaw(payload)
        self._status = status
        self.headers = {"Content-Length": str(len(payload))}

    def raise_for_status(self):
        if self._status >= 400:
            import requests

            raise requests.HTTPError(f"status {self._status}")


def _patch_get(monkeypatch, response):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: response)


def test_fetch_manifest_parses_response(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(json.dumps(_doc()).encode()))
    assert manifest_mod.fetch_manifest("https://example.invalid/m.json").version == "1.0.1"


def test_fetch_manifest_rejects_http_url():
    with pytest.raises(UpdateError):
        manifest_mod.fetch_manifest("http://example.invalid/m.json")


def test_fetch_manifest_rejects_bad_json(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(b"not json at all"))
    with pytest.raises(UpdateError):
        manifest_mod.fetch_manifest("https://example.invalid/m.json")


def test_fetch_manifest_refuses_oversized_body(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(b"x" * (manifest_mod._MANIFEST_MAX_BYTES + 10)))
    with pytest.raises(UpdateError) as excinfo:
        manifest_mod.fetch_manifest("https://example.invalid/m.json")
    assert "large" in str(excinfo.value)


def test_manifest_url_env_override(monkeypatch):
    monkeypatch.setenv("GUITAR_HELPER_UPDATE_URL", "https://localhost/test.json")
    assert manifest_mod.manifest_url() == "https://localhost/test.json"


def test_check_for_update_returns_none_when_current(monkeypatch):
    monkeypatch.setattr(
        service_mod, "fetch_manifest", lambda *a, **k: parse_manifest(_doc(version="1.0.0"))
    )
    assert service_mod.check_for_update(current="1.0.0") is None


def test_check_for_update_returns_manifest_when_newer(monkeypatch):
    monkeypatch.setattr(
        service_mod, "fetch_manifest", lambda *a, **k: parse_manifest(_doc(version="2.0.0"))
    )
    assert service_mod.check_for_update(current="1.0.0").version == "2.0.0"


# --- verified download -------------------------------------------------------


class _FakeDownload:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._payload), chunk_size):
            yield self._payload[i : i + chunk_size]


def _patch_download(monkeypatch, payload: bytes):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeDownload(payload))


def _manifest_for(payload: bytes) -> UpdateManifest:
    return parse_manifest(_doc(sha256=hashlib.sha256(payload).hexdigest()))


def test_download_writes_and_verifies(tmp_path, monkeypatch):
    payload = b"pretend installer bytes" * 100
    _patch_download(monkeypatch, payload)

    out = service_mod.download_update(_manifest_for(payload), dest_dir=tmp_path)

    assert out.read_bytes() == payload
    assert out.name == "GuitarHelper-Setup-1.0.1.exe"


def test_download_rejects_and_deletes_on_digest_mismatch(tmp_path, monkeypatch):
    _patch_download(monkeypatch, b"tampered payload")
    manifest = _manifest_for(b"the payload that was promised")

    with pytest.raises(UpdateVerificationError):
        service_mod.download_update(manifest, dest_dir=tmp_path)

    # The unverified bytes must not survive anywhere on disk.
    assert list(tmp_path.iterdir()) == []


def test_download_reports_progress(tmp_path, monkeypatch):
    payload = b"x" * (service_mod._CHUNK * 3)
    _patch_download(monkeypatch, payload)
    seen: list[tuple[int, int]] = []

    service_mod.download_update(
        _manifest_for(payload), dest_dir=tmp_path, progress=lambda d, t: seen.append((d, t))
    )

    assert seen[-1] == (len(payload), len(payload))
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_download_cancel_removes_partial_file(tmp_path, monkeypatch):
    payload = b"y" * (service_mod._CHUNK * 4)
    _patch_download(monkeypatch, payload)

    with pytest.raises(UpdateError):
        service_mod.download_update(
            _manifest_for(payload), dest_dir=tmp_path, should_cancel=lambda: True
        )

    assert list(tmp_path.iterdir()) == []


# --- installer handoff -------------------------------------------------------


def test_launch_installer_rejects_missing_file(tmp_path):
    with pytest.raises(UpdateError):
        service_mod.launch_installer(tmp_path / "nope.exe")


def test_launch_installer_spawns_detached_with_silent_flags(tmp_path, monkeypatch):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"MZ")
    captured = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(service_mod.subprocess, "Popen", _fake_popen)
    service_mod.launch_installer(installer)

    assert captured["cmd"][0] == str(installer)
    assert "/SILENT" in captured["cmd"]
    assert "/CLOSEAPPLICATIONS" in captured["cmd"]
    # Without a detached process group the installer dies with this process,
    # which is exactly the moment it needs to still be alive.
    assert captured["kwargs"]["creationflags"] != 0


def test_launch_installer_wraps_os_error(tmp_path, monkeypatch):
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"MZ")

    def _boom(*a, **k):
        raise OSError("denied")

    monkeypatch.setattr(service_mod.subprocess, "Popen", _boom)
    with pytest.raises(UpdateError):
        service_mod.launch_installer(installer)


def test_current_version_matches_package():
    import guitar_helper

    assert manifest_mod.current_version() == guitar_helper.__version__
    parse_version(guitar_helper.__version__)


def test_default_manifest_url_is_https():
    assert manifest_mod.DEFAULT_MANIFEST_URL.startswith("https://")


def test_update_package_is_qt_free():
    # Tier discipline (ISSUE-007): this package must be importable and testable
    # without Qt, so the Qt surface stays in ui/update_worker.py.
    import guitar_helper.update as pkg

    source = Path(pkg.__file__).parent
    for module in source.glob("*.py"):
        assert "PySide6" not in module.read_text(encoding="utf-8"), module.name


# --- GPL source offer travels with the manifest ------------------------------


def test_source_url_is_parsed():
    m = parse_manifest(_doc(source_url="https://example.invalid/src"))
    assert m.source_url == "https://example.invalid/src"


def test_source_url_is_optional():
    assert parse_manifest(_doc()).source_url is None


def test_blank_source_url_is_none_not_empty_string():
    assert parse_manifest(_doc(source_url="")).source_url is None


def test_source_url_must_be_https():
    # A source offer pointing at a downgradeable URL is not much of an offer.
    with pytest.raises(UpdateError) as excinfo:
        parse_manifest(_doc(source_url="http://example.invalid/src"))
    assert "https" in str(excinfo.value)


def test_fetch_manifest_tolerates_a_utf8_bom(monkeypatch):
    """PowerShell's Out-File -Encoding utf8 writes a BOM, so the manifest this
    project publishes has one. A plain utf-8 decode rejects it and every live
    update check failed - silently, because a failed check is quiet by design."""
    payload = b"\xef\xbb\xbf" + json.dumps(_doc()).encode("utf-8")
    _patch_get(monkeypatch, _FakeResponse(payload))

    assert manifest_mod.fetch_manifest("https://example.invalid/m.json").version == "1.0.1"


def test_fetch_manifest_still_parses_without_a_bom(monkeypatch):
    _patch_get(monkeypatch, _FakeResponse(json.dumps(_doc()).encode("utf-8")))
    assert manifest_mod.fetch_manifest("https://example.invalid/m.json").version == "1.0.1"
