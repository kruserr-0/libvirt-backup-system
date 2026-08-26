"""Debian/Ubuntu system-dependency detection for install and preflight.

The system shells out to ``virsh``, ``qemu-nbd``, ``qemu-img``, and
``nbdcopy``; all four come from OS packages. This module detects the host's
Debian/Ubuntu release, verifies each binary is present AND runnable (a
binary with a missing shared library resolves on PATH but cannot execute,
which previously let ``check`` pass on a broken host), and maps the missing
binaries onto the exact ``apt-get install`` command for the detected
release.

Policy: the installer never installs OS packages behind the operator's
back and never side-loads packages built for a different OS release. An
interactive ``install`` offers to run the OS's own ``apt-get`` after
explicit confirmation — sudo credentials are requested interactively by
``sudo`` itself, used only for the apt-get commands, and dropped again
with ``sudo -k`` afterwards. Every other path (declined, ``--non-interactive``,
unknown release, apt failure) prints a copy-paste command for the detected
release and exits before any install step has mutated the system.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .config import parse_env_file, prefixed
from .logging_json import event
from .shell import CommandError, run

# Binaries that must come from OS packages (kopia is pin-installed by the
# installer itself; df ships with coreutils on every supported release).
SYSTEM_DEP_BINARIES = ("virsh", "qemu-nbd", "qemu-img", "nbdcopy")
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
KOPIA_INSTALL_HINT = "kopia is installed by 'libvirt-backup-system install'; re-run install"
BINARY_PROBE_TIMEOUT_SECONDS = 10


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


def _binary_runs(binary_path: str) -> bool:
    try:
        result = run([binary_path, "--version"], check=False, timeout=BINARY_PROBE_TIMEOUT_SECONDS)
    except (OSError, CommandError):
        return False
    return result.returncode == 0


def binary_failure(binary: str) -> str | None:
    """One failure string when ``binary`` is missing or broken, else None.

    "Present but not runnable" catches the half-broken state where the file
    exists on PATH but cannot execute (missing shared library, truncated
    download): ``shutil.which`` alone reported such a host as healthy.
    """
    located = shutil.which(binary)
    if located is None:
        return f"missing binary: {binary}"
    if not _binary_runs(located):
        return f"binary present but not runnable: {binary} ({located}); reinstall its package"
    return None


def missing_system_deps() -> list[str]:
    return [binary for binary in SYSTEM_DEP_BINARIES if binary_failure(binary) is not None]


def apt_packages(binaries: Iterable[str]) -> list[str]:
    return sorted({APT_PACKAGE_FOR_BINARY[binary] for binary in binaries if binary in APT_PACKAGE_FOR_BINARY})


def apt_install_command(binaries: Iterable[str]) -> str:
    return "sudo apt-get update && sudo apt-get install -y " + " ".join(apt_packages(binaries))


def dependency_hint(missing: list[str], prefix: Path | None = None) -> str:
    """Single-line remediation hint for preflight failure lists."""
    os_release = detect_os(prefix)
    named = ", ".join(missing)
    if release_supported(os_release):
        return f"install the missing packages with: {apt_install_command(missing)}"
    if os_release.os_id in KNOWN_APT_RELEASES:
        return (
            f"{os_release.pretty_name or os_release.os_id} is not a release this tool was tested against; "
            f"try: {apt_install_command(missing)} -- if the package names changed on your release, search "
            f"the web or ask an AI assistant (Google/ChatGPT) how to install {named} on it"
        )
    return (
        "this OS is not a known Debian/Ubuntu release; search the web or ask an AI assistant "
        f"(Google/ChatGPT) how to install {named} on it"
    )


def required_binary_failures(binaries: Iterable[str], prefix: Path | None = None) -> list[str]:
    """Presence + runnability failures for ``binaries``, with remediation hints."""
    failures: list[str] = []
    missing_deps: list[str] = []
    kopia_failed = False
    for binary in binaries:
        failure = binary_failure(binary)
        if failure is None:
            continue
        failures.append(failure)
        if binary in APT_PACKAGE_FOR_BINARY:
            missing_deps.append(binary)
        if binary == "kopia":
            kopia_failed = True
    if missing_deps:
        failures.append(dependency_hint(missing_deps, prefix))
    if kopia_failed:
        failures.append(KOPIA_INSTALL_HINT)
    return failures


def dependency_error_lines(os_release: OsRelease, missing: list[str]) -> list[str]:
    named = ", ".join(f"{binary} ({APT_PACKAGE_FOR_BINARY[binary]})" for binary in missing)
    lines = [
        "",
        f"Missing system dependencies: {named}",
        f"Detected OS: {os_release.pretty_name or os_release.os_id or 'unknown'}",
        "",
    ]
    if release_supported(os_release):
        lines += [
            "Install them with:",
            "",
            f"  {apt_install_command(missing)}",
            "",
            "then re-run this command.",
        ]
    elif os_release.os_id in KNOWN_APT_RELEASES:
        known = ", ".join(KNOWN_APT_RELEASES[os_release.os_id])
        lines += [
            f"This {os_release.os_id} release ({os_release.version_id or 'unknown version'}) is not one this",
            f"tool was tested against (known: {known}). The command below worked on",
            "those releases and is likely still correct:",
            "",
            f"  {apt_install_command(missing)}",
            "",
            "If the package names have changed on your release, search the web or ask",
            f"an AI assistant (Google/ChatGPT) how to install {', '.join(missing)}",
            f"on {os_release.pretty_name or os_release.os_id}.",
        ]
    else:
        lines += [
            "This does not look like a Debian or Ubuntu system, so no install command",
            "can be suggested. Search the web or ask an AI assistant (Google/ChatGPT)",
            f"how to install {', '.join(missing)} on your OS, then re-run this command.",
        ]
    lines += [
        "",
        "Nothing was installed and nothing was modified; this command never",
        "installs OS packages without your consent.",
        "",
    ]
    return lines


def _print_lines(lines: list[str]) -> None:
    print("\n".join(lines), file=sys.stderr, flush=True)


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (OSError, ValueError):
        return False


def _confirm(prompt: str) -> bool:
    print(prompt, end="", file=sys.stderr, flush=True)
    try:
        answer = input()
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _run_visible(args: list[str]) -> int:
    """Run a command with inherited stdio so apt progress and sudo's own
    password prompt reach the operator's terminal directly (the password is
    read by sudo on the TTY and never passes through this process)."""
    try:
        completed = subprocess.run(args, check=False)
    except OSError as exc:
        event("error", "command could not be started", command=args[0], error=str(exc))
        return 1
    return completed.returncode


def _apt_install(packages: list[str], *, as_root: bool) -> bool:
    sudo_prefix = [] if as_root else ["sudo"]
    if not as_root and _run_visible(["sudo", "-v"]) != 0:
        event("error", "sudo authentication failed; cannot install system dependencies")
        return False
    try:
        ok = _run_visible([*sudo_prefix, "apt-get", "update"]) == 0
        ok = ok and _run_visible([*sudo_prefix, "apt-get", "install", "-y", *packages]) == 0
    finally:
        if not as_root:
            # Drop the cached sudo credentials: they were requested for the
            # apt-get commands above and must not leak into later commands.
            _run_visible(["sudo", "-k"])
    if not ok:
        event("error", "apt-get failed to install system dependencies", packages=" ".join(packages))
    return ok


def ensure_system_deps(root: Path, *, non_interactive: bool = False) -> int:
    """Gate install on the system dependencies; return a process exit code.

    Runs before anything is mutated so a missing dependency aborts the whole
    install up front instead of leaving a half-installed system behind.
    """
    if root != Path("/"):
        # Sandboxed installs (tests, --prefix) must not probe or mutate the
        # host OS; preflight still validates binaries at check/run time.
        event("info", "system dependency check skipped for sandboxed prefix", root_prefix=str(root))
        return 0
    missing = missing_system_deps()
    if not missing:
        return 0
    os_release = detect_os(root)
    event("error", "missing system dependencies", binaries=",".join(missing))
    interactive = _stdin_is_tty() and not non_interactive
    if not interactive or not release_supported(os_release):
        _print_lines(dependency_error_lines(os_release, missing))
        return 1
    packages = apt_packages(missing)
    _print_lines(
        [
            "",
            f"Missing system dependencies: {', '.join(missing)}",
            f"Detected OS: {os_release.pretty_name or os_release.os_id}",
            f"They can be installed now with: apt-get update && apt-get install -y {' '.join(packages)}",
        ]
    )
    if not _confirm("Install these packages now via apt-get? [y/N] "):
        _print_lines(dependency_error_lines(os_release, missing))
        return 1
    as_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if not _apt_install(packages, as_root=as_root):
        _print_lines(dependency_error_lines(os_release, missing))
        return 1
    still_missing = missing_system_deps()
    if still_missing:
        event("error", "system dependencies still missing after apt-get install", binaries=",".join(still_missing))
        _print_lines(dependency_error_lines(os_release, still_missing))
        return 1
    event("info", "installed system dependencies", packages=" ".join(packages))
    return 0
