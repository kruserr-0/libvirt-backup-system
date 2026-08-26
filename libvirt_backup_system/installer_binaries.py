"""Pinned-version binary installer for kopia.

The system shells out to ``kopia``, which is not packaged by Debian/Ubuntu;
``install`` wires it in by extracting the vendored pinned tarball (or
downloading the same pinned upstream release) after verifying a pinned
sha256, then dropping the binary into place atomically.

The other external binaries (``virsh``, ``qemu-nbd``, ``qemu-img``,
``nbdcopy``) come from OS packages and are deliberately NOT installed here:
``installer_deps`` checks them up front and either installs them via the
host's own apt (with the operator's explicit consent) or aborts the install
with a copy-paste command for the detected release. Side-loading .debs
built for a different OS release is exactly the half-broken state that
policy exists to prevent.

Network: a kopia install without the vendored tarball makes one outbound
HTTPS request against ``github.com``. A pre-placed binary at the pinned
version lets an offline host skip the call (see ``docs/install.md`` for
the offline procedure).

Determinism: the download is sha256-verified against a constant pinned in
``kopia_vendor`` before any extract step runs. A mismatch raises
``BinaryInstallError`` so a bad pin or a tampered mirror fails loudly
instead of silently installing the wrong bits.
"""

from __future__ import annotations

import hashlib
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import prefixed
from .kopia_vendor import (
    KOPIA_SHA256,
    KOPIA_URL,
    KOPIA_VERSION,
    KopiaVendorError,
    vendored_kopia_tarball_bytes,
)
from .kopia_vendor import extract_kopia_binary as _extract_kopia_binary
from .logging_json import event
from .shell import CommandError, run


class BinaryInstallError(RuntimeError):
    """Raised when downloading, verifying, or installing a pinned binary fails."""


@dataclass(frozen=True)
class _BinaryPin:
    name: str
    url: str
    sha256: str


def _download(url: str) -> bytes:
    """Fetch ``url`` and return the raw bytes; raise BinaryInstallError on failure."""
    try:
        # ``urllib.request.urlopen`` follows redirects (kopia GitHub releases
        # 302 from github.com to objects.githubusercontent.com) and returns
        # the asset bytes; we keep the whole response in memory because the
        # pinned artifact is ~10-20 MB.
        with urllib.request.urlopen(url) as response:  # noqa: S310
            data = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise BinaryInstallError(
            f"failed to download {url}: {exc}; see docs/install.md for the manual install procedure"
        ) from exc
    if not isinstance(data, bytes):  # pragma: no cover - defensive
        raise BinaryInstallError(f"download of {url} returned non-bytes payload")
    return data


def _verify_sha256(data: bytes, expected: str, *, source: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise BinaryInstallError(
            f"sha256 mismatch for {source}: expected {expected}, got {actual}; " "refusing to install untrusted bytes"
        )


def _fetch_pinned(pin: _BinaryPin) -> bytes:
    vendored = vendored_kopia_tarball_bytes()
    if vendored is not None:
        event("info", "using vendored pinned binary", name=pin.name, sha256=pin.sha256)
        return vendored
    event("info", "downloading pinned binary", name=pin.name, url=pin.url)
    data = _download(pin.url)
    _verify_sha256(data, pin.sha256, source=pin.url)
    event("info", "verified pinned binary", name=pin.name, sha256=pin.sha256)
    return data


def _kopia_installed_version(kopia_path: Path) -> str | None:
    """Return the version reported by ``kopia --version`` or None if unusable."""
    if not kopia_path.exists():
        return None
    try:
        result = run([str(kopia_path), "--version"], check=False, timeout=10)
    except (OSError, CommandError):
        return None
    if result.returncode != 0:
        return None
    # ``kopia --version`` prints e.g. "0.17.0 build: abcd from: ...".
    parts = result.stdout.strip().split()
    return parts[0] if parts else None


def install_kopia(prefix: Path | None = None) -> None:
    """Install kopia at the pinned version into ``/usr/local/bin/kopia``.

    Idempotent: if the binary is already on disk and reports the pinned
    version, the network round-trip is skipped entirely. Otherwise the
    pinned tarball is fetched, sha256-verified, extracted, and atomically
    moved into place.

    Raises ``BinaryInstallError`` on download / verify / extract failure
    so the installer can hard-fail the whole install.
    """
    root = prefix if prefix is not None else Path("/")
    kopia_path = prefixed("/usr/local/bin/kopia", root)
    installed = _kopia_installed_version(kopia_path)
    if installed == KOPIA_VERSION:
        event("info", "kopia already installed at pinned version", path=str(kopia_path), version=installed)
        return
    pin = _BinaryPin(name="kopia", url=KOPIA_URL, sha256=KOPIA_SHA256)
    tarball_bytes = _fetch_pinned(pin)
    try:
        _extract_kopia_binary(tarball_bytes, kopia_path)
    except (OSError, tarfile.TarError, KopiaVendorError) as exc:
        raise BinaryInstallError(f"failed to extract kopia tarball: {exc}") from exc
    event("info", "installed kopia", path=str(kopia_path), version=KOPIA_VERSION)
