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
from pathlib import Path

from .installer_deps_os import (
    APT_PACKAGE_FOR_BINARY,
    KNOWN_APT_RELEASES,
    SYSTEM_DEP_BINARIES,
    OsRelease,
    apt_install_command,
    apt_packages,
    detect_os,
    os_release_label,
    release_supported,
    search_prompt,
)
from .logging_json import event
from .shell import CommandError, run

KOPIA_INSTALL_HINT = "kopia is installed by 'libvirt-backup-system install'; re-run install"
BINARY_PROBE_TIMEOUT_SECONDS = 10


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


def dependency_hint(missing: list[str], prefix: Path | None = None) -> str:
    """Single-line remediation hint for preflight failure lists."""
    os_release = detect_os(prefix)
    named = ", ".join(missing)
    if release_supported(os_release):
        return f"install the missing packages with: {apt_install_command(missing)}"
    if os_release.os_id in KNOWN_APT_RELEASES:
        return (
            f"{os_release_label(os_release)} is not a version this tool was tested against; "
            f"try: {apt_install_command(missing)} -- if the package names changed on your version, paste "
            f"this into Google or ChatGPT: {search_prompt(os_release, missing)!r}"
        )
    return (
        f"this OS ({os_release_label(os_release)}) is not a known Debian/Ubuntu release; paste this into "
        f"Google or ChatGPT to get the install steps for {named}: {search_prompt(os_release, missing)!r}"
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
        f"Detected OS: {os_release_label(os_release)}",
        "",
    ]
    if release_supported(os_release):
        lines += [
            "Install them with:",
            "",
            f"  {apt_install_command(missing)}",
            "",
            "then re-run this command. Or re-run install with -y to let it run",
            "apt-get for you (--non-interactive -y for automation tooling; that",
            "needs root or passwordless sudo and is skipped otherwise).",
        ]
    elif os_release.os_id in KNOWN_APT_RELEASES:
        known = ", ".join(KNOWN_APT_RELEASES[os_release.os_id])
        lines += [
            f"Your {os_release.os_id} version ({os_release.version_id or 'unknown version'}) is not one this",
            f"tool was tested against (known: {known}). The command below worked on",
            "those versions and is likely still correct:",
            "",
            f"  {apt_install_command(missing)}",
            "",
            "If the package names have changed on your version, copy-paste this",
            "question into Google or ChatGPT to get the install steps:",
            "",
            f"  {search_prompt(os_release, missing)}",
        ]
    else:
        lines += [
            "This does not look like a Debian or Ubuntu system, so no install command",
            "can be suggested. Copy-paste this question into Google or ChatGPT to",
            "get the install steps for your OS, then re-run this command:",
            "",
            f"  {search_prompt(os_release, missing)}",
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


def _apt_install(packages: list[str], *, as_root: bool, interactive: bool, reinstall: bool = False) -> bool:
    if as_root:
        sudo_prefix: list[str] = []
    elif interactive:
        # sudo prompts for its password on the TTY; the credentials are used
        # only for the apt-get commands below and dropped again afterwards.
        sudo_prefix = ["sudo"]
        if _run_visible(["sudo", "-v"]) != 0:
            event("error", "sudo authentication failed; cannot install system dependencies")
            return False
    else:
        # Non-interactive (--non-interactive -y): only proceed when sudo works
        # without a password (NOPASSWD or cached credentials); otherwise skip
        # the automatic install attempt instead of hanging on a prompt.
        sudo_prefix = ["sudo", "-n"]
        if _run_visible(["sudo", "-n", "-v"]) != 0:
            event("info", "sudo is not available without a password; skipping automatic dependency install")
            return False
    # DEBIAN_FRONTEND=noninteractive keeps debconf from prompting when no
    # operator is watching; interactive runs keep the default frontend.
    env_prefix = [] if interactive else ["env", "DEBIAN_FRONTEND=noninteractive"]
    reinstall_args = ["--reinstall"] if reinstall else []
    try:
        ok = _run_visible([*sudo_prefix, *env_prefix, "apt-get", "update"]) == 0
        ok = (
            ok
            and _run_visible([*sudo_prefix, *env_prefix, "apt-get", "install", "-y", *reinstall_args, *packages]) == 0
        )
    finally:
        if not as_root:
            # Drop the cached sudo credentials: they were requested for the
            # apt-get commands above and must not leak into later commands.
            _run_visible(["sudo", "-k"])
    if not ok:
        event("error", "apt-get failed to install system dependencies", packages=" ".join(packages))
    return ok


def ensure_system_deps(
    root: Path,
    *,
    non_interactive: bool = False,
    assume_yes: bool = False,
    reinstall: bool = False,
) -> int:
    """Gate install on the system dependencies; return a process exit code.

    Runs before anything is mutated so a missing dependency aborts the whole
    install up front instead of leaving a half-installed system behind.
    Dependencies already on the system are never installed again — apt only
    ever runs for the missing/broken ones.

    ``assume_yes`` (install ``-y``) answers the apt-install confirmation
    automatically; combined with ``non_interactive`` it lets automation
    tooling apt-install missing dependencies when running as root or with
    passwordless sudo (skipped gracefully otherwise). ``reinstall``
    (install ``--reinstall-deps``) forces ``apt-get install --reinstall`` of
    ALL dependency packages, present or not, to repair a broken install.
    """
    if root != Path("/"):
        # Sandboxed installs (tests, --prefix) must not probe or mutate the
        # host OS; preflight still validates binaries at check/run time.
        event("info", "system dependency check skipped for sandboxed prefix", root_prefix=str(root))
        return 0
    missing = missing_system_deps()
    if not missing and not reinstall:
        return 0
    targets = list(SYSTEM_DEP_BINARIES) if reinstall else missing
    os_release = detect_os(root)
    if missing:
        event("error", "missing system dependencies", binaries=",".join(missing))
    else:
        event("info", "reinstalling system dependencies on request", binaries=",".join(targets))
    interactive = _stdin_is_tty() and not non_interactive
    may_install = (interactive or assume_yes) and release_supported(os_release)
    if not may_install:
        _print_lines(dependency_error_lines(os_release, targets))
        return 1
    packages = apt_packages(targets)
    verb = "reinstalled" if reinstall else "installed"
    _print_lines(
        [
            "",
            f"{'Reinstalling' if reinstall else 'Missing'} system dependencies: {', '.join(targets)}",
            f"Detected OS: {os_release_label(os_release)}",
            f"They can be {verb} now with: apt-get update && apt-get install -y {' '.join(packages)}",
        ]
    )
    if not assume_yes and not _confirm(
        f"{'Reinstall' if reinstall else 'Install'} these packages now via apt-get? [y/N] "
    ):
        _print_lines(dependency_error_lines(os_release, targets))
        return 1
    as_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if not _apt_install(packages, as_root=as_root, interactive=interactive, reinstall=reinstall):
        _print_lines(dependency_error_lines(os_release, targets))
        return 1
    still_missing = missing_system_deps()
    if still_missing:
        event("error", "system dependencies still missing after apt-get install", binaries=",".join(still_missing))
        _print_lines(dependency_error_lines(os_release, still_missing))
        return 1
    event("info", f"{verb} system dependencies", packages=" ".join(packages))
    return 0
