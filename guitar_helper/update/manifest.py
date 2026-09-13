"""Update manifest: fetching, parsing and version comparison."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import guitar_helper

DEFAULT_MANIFEST_URL = (
    "https://github.com/VespralSquid/guitar-helper/releases/latest/download/manifest.json"
)

_ENV_MANIFEST_URL = "GUITAR_HELPER_UPDATE_URL"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_MANIFEST_MAX_BYTES = 64 * 1024


class UpdateError(RuntimeError):
    """Any failure in the update flow. Never fatal — the app runs regardless."""


class UpdateVerificationError(UpdateError):
    """A download did not match the digest the manifest promised.

    Treated as hostile rather than as a transient error: the partial file is
    deleted and the flow stops, because the only ways to get here are a
    corrupted transfer or a tampered one and neither should be executed.
    """


def current_version() -> str:
    return guitar_helper.__version__


def manifest_url() -> str:
    """The configured manifest URL. The env var exists so a release can be
    rehearsed against a local file server without a code change."""
    return os.environ.get(_ENV_MANIFEST_URL) or DEFAULT_MANIFEST_URL


def parse_version(text: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(str(text).strip())
    if match is None:
        raise UpdateError(f"not a MAJOR.MINOR.PATCH version: {text!r}")
    return (int(match[1]), int(match[2]), int(match[3]))


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    url: str
    sha256: str
    notes: str = ""
    min_upgradable_from: str | None = None
    # GPLv3 section 6: conveying a binary obliges us to tell the recipient how to
    # get the corresponding source. An update conveys a binary, so the offer has
    # to travel with the manifest rather than only with the first install.
    source_url: str | None = None

    def is_newer_than(self, current: str) -> bool:
        return is_newer(self.version, current)

    def upgradable_from(self, current: str) -> bool:
        """False when the installed build is too old for this installer to
        upgrade in place and needs a manual reinstall."""
        if self.min_upgradable_from is None:
            return True
        return parse_version(current) >= parse_version(self.min_upgradable_from)


def _require_https(url: str, what: str) -> str:
    url = str(url).strip()
    if not url.lower().startswith("https://"):
        raise UpdateError(f"{what} must be an https:// URL, got {url!r}")
    return url


def parse_manifest(data: object) -> UpdateManifest:
    """Validate an untrusted manifest document.

    Everything here arrives over the network, so each field is checked before
    use rather than trusted and passed on: a bad digest length or a non-HTTPS
    download URL has to fail here, not at execution time.
    """
    if not isinstance(data, dict):
        raise UpdateError(f"manifest must be a JSON object, got {type(data).__name__}")

    missing = [key for key in ("version", "url", "sha256") if not data.get(key)]
    if missing:
        raise UpdateError(f"manifest is missing required field(s): {', '.join(missing)}")

    version = str(data["version"]).strip()
    parse_version(version)

    digest = str(data["sha256"]).strip().lower()
    if not _SHA256_RE.match(digest):
        raise UpdateError("manifest sha256 is not a 64-character hex digest")

    minimum = data.get("min_upgradable_from")
    if minimum is not None:
        minimum = str(minimum).strip()
        parse_version(minimum)

    source = data.get("source_url")
    if source:
        source = _require_https(source, "manifest source url")
    else:
        source = None

    return UpdateManifest(
        version=version,
        url=_require_https(data["url"], "manifest download url"),
        sha256=digest,
        notes=str(data.get("notes") or ""),
        min_upgradable_from=minimum,
        source_url=source,
    )


def fetch_manifest(url: str | None = None, *, timeout: float = 10.0) -> UpdateManifest:
    import requests  # noqa: PLC0415 — keeps requests off the startup import path

    target = _require_https(url or manifest_url(), "manifest url")
    try:
        response = requests.get(target, timeout=timeout, stream=True)
        response.raise_for_status()
        # A manifest is a few hundred bytes; refusing to buffer more than 64 KB
        # stops a hostile or misconfigured endpoint from streaming forever.
        body = response.raw.read(_MANIFEST_MAX_BYTES + 1, decode_content=True)
    except requests.RequestException as exc:
        raise UpdateError(f"could not reach the update server: {exc}") from exc

    if len(body) > _MANIFEST_MAX_BYTES:
        raise UpdateError("manifest is implausibly large; refusing to parse")

    import json  # noqa: PLC0415

    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"manifest is not valid JSON: {exc}") from exc

    return parse_manifest(document)
