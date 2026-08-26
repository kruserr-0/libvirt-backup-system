"""OS-release detection and apt-package mapping for the dependency gate.

Split out of ``installer_deps`` to keep both files under the project's
300-LOC ceiling; ``installer_deps`` re-exports these names.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .config import parse_env_file, prefixed

# Binaries that must come from OS packages (kopia is pin-installed by the
# installer itself; df ships with coreutils on every supported release).
SYSTEM_DEP_BINARIES: tuple[str, ...] = ("virsh", "qemu-nbd", "qemu-img", "nbdcopy")
APT_PACKAGE_FOR_BINARY = {
    "virsh": "libvirt-clients",
    "qemu-nbd": "qemu-utils",
    "qemu-img": "qemu-utils",
    "nbdcopy": "libnbd-bin",
}
# Releases the apt package names above were verified against. A release
# missing from this map still gets the command printed as a best guess, plus
# a pointer to research the right packages for that release.
KNOWN_APT_RELEASES: dict[str, tuple[str, ...]] = {
    "debian": ("11", "12", "13"),
    "ubuntu": ("20.04", "22.04", "24.04", "24.10", "25.04"),
}


@dataclass(frozen=True)
class OsRelease:
    os_id: str
    version_id: str
    pretty_name: str


def detect_os(prefix: Path | None = None) -> OsRelease:
    path = prefixed("/etc/os-release", prefix if prefix is not None else Path("/"))
    try:
        values = parse_env_file(path)
    except OSError:
        values = {}
    return OsRelease(
        os_id=values.get("ID", "").strip().lower(),
        version_id=values.get("VERSION_ID", "").strip(),
        pretty_name=values.get("PRETTY_NAME", "").strip(),
    )


def release_supported(os_release: OsRelease) -> bool:
    return os_release.version_id in KNOWN_APT_RELEASES.get(os_release.os_id, ())


def apt_packages(binaries: Iterable[str]) -> list[str]:
    return sorted({APT_PACKAGE_FOR_BINARY[binary] for binary in binaries if binary in APT_PACKAGE_FOR_BINARY})


def apt_install_command(binaries: Iterable[str]) -> str:
    return "sudo apt-get update && sudo apt-get install -y " + " ".join(apt_packages(binaries))


def os_release_label(os_release: OsRelease) -> str:
    """Human-readable OS + version, e.g. 'Debian GNU/Linux 14 (forky) (version 14)'."""
    if os_release.pretty_name:
        if os_release.version_id and os_release.version_id not in os_release.pretty_name:
            return f"{os_release.pretty_name} (version {os_release.version_id})"
        return os_release.pretty_name
    if os_release.os_id:
        return f"{os_release.os_id} {os_release.version_id}".strip()
    return "unknown"


def search_prompt(os_release: OsRelease, missing: list[str]) -> str:
    """A copy-paste question for Google/ChatGPT that names the deps and OS version."""
    label = os_release_label(os_release)
    target = label if label != "unknown" else "my operating system"
    return f"How do I install the packages that provide {', '.join(missing)} on {target}?"
