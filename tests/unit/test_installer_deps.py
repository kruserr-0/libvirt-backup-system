from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from libvirt_backup_system import installer_deps
from libvirt_backup_system.installer_deps import OsRelease
from libvirt_backup_system.shell import CommandError, CommandResult


def _write_os_release(tmp_path: Path, text: str) -> None:
    path = tmp_path / "etc/os-release"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


DEBIAN12 = OsRelease(os_id="debian", version_id="12", pretty_name="Debian GNU/Linux 12 (bookworm)")
DEBIAN99 = OsRelease(os_id="debian", version_id="99", pretty_name="Debian GNU/Linux 99 (futuristic)")
MACOS = OsRelease(os_id="", version_id="", pretty_name="")


# --- detect_os / release_supported ------------------------------------------


def test_detect_os_parses_os_release(tmp_path: Path) -> None:
    _write_os_release(tmp_path, 'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n')
    assert installer_deps.detect_os(tmp_path) == DEBIAN12


def test_detect_os_missing_file_returns_empty(tmp_path: Path) -> None:
    assert installer_deps.detect_os(tmp_path) == OsRelease(os_id="", version_id="", pretty_name="")


def test_detect_os_unreadable_file_returns_empty(tmp_path: Path) -> None:
    # A directory at the os-release path makes read_text raise OSError.
    (tmp_path / "etc/os-release").mkdir(parents=True)
    assert installer_deps.detect_os(tmp_path) == OsRelease(os_id="", version_id="", pretty_name="")


def test_release_supported() -> None:
    assert installer_deps.release_supported(DEBIAN12)
    assert installer_deps.release_supported(OsRelease("ubuntu", "24.04", "Ubuntu 24.04 LTS"))
    assert not installer_deps.release_supported(DEBIAN99)
    assert not installer_deps.release_supported(OsRelease("arch", "", "Arch Linux"))


# --- binary probing ----------------------------------------------------------


def test_binary_failure_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer_deps.shutil, "which", lambda _name: None)
    assert installer_deps.binary_failure("virsh") == "missing binary: virsh"


def test_binary_failure_not_runnable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer_deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(installer_deps, "_binary_runs", lambda _path: False)
    assert installer_deps.binary_failure("nbdcopy") == (
        "binary present but not runnable: nbdcopy (/usr/bin/nbdcopy); reinstall its package"
    )


def test_binary_failure_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer_deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(installer_deps, "_binary_runs", lambda _path: True)
    assert installer_deps.binary_failure("virsh") is None


def test_binary_runs_probe_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    def make_run(rc: int) -> Any:
        return lambda args, **kwargs: CommandResult(args=args, returncode=rc, stdout="", stderr="")

    monkeypatch.setattr(installer_deps, "run", make_run(0))
    assert installer_deps._binary_runs("/usr/bin/virsh")
    monkeypatch.setattr(installer_deps, "run", make_run(2))
    assert not installer_deps._binary_runs("/usr/bin/virsh")

    def boom_os(args: list[str], **kwargs: Any) -> CommandResult:
        raise OSError("exec format error")

    monkeypatch.setattr(installer_deps, "run", boom_os)
    assert not installer_deps._binary_runs("/usr/bin/virsh")

    def boom_cmd(args: list[str], **kwargs: Any) -> CommandResult:
        raise CommandError(CommandResult(args=args, returncode=1, stdout="", stderr=""))

    monkeypatch.setattr(installer_deps, "run", boom_cmd)
    assert not installer_deps._binary_runs("/usr/bin/virsh")


def test_missing_system_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer_deps.shutil, "which", lambda name: None if name == "nbdcopy" else f"/usr/bin/{name}")
    monkeypatch.setattr(installer_deps, "_binary_runs", lambda _path: True)
    assert installer_deps.missing_system_deps() == ["nbdcopy"]


# --- apt command + hints -----------------------------------------------------


def test_apt_packages_dedupes_and_sorts() -> None:
    assert installer_deps.apt_packages(["qemu-img", "qemu-nbd", "virsh", "df"]) == ["libvirt-clients", "qemu-utils"]


def test_apt_install_command() -> None:
    assert installer_deps.apt_install_command(["nbdcopy"]) == (
        "sudo apt-get update && sudo apt-get install -y libnbd-bin"
    )


def test_dependency_hint_supported(tmp_path: Path) -> None:
    _write_os_release(tmp_path, "ID=debian\nVERSION_ID=12\n")
    hint = installer_deps.dependency_hint(["virsh"], tmp_path)
    assert hint == "install the missing packages with: sudo apt-get update && sudo apt-get install -y libvirt-clients"


def test_dependency_hint_newer_release(tmp_path: Path) -> None:
    _write_os_release(tmp_path, 'ID=debian\nVERSION_ID=99\nPRETTY_NAME="Debian GNU/Linux 99"\n')
    hint = installer_deps.dependency_hint(["virsh"], tmp_path)
    assert "not a version this tool was tested against" in hint
    assert "apt-get install -y libvirt-clients" in hint
    assert "paste this into Google or ChatGPT" in hint
    assert "'How do I install the packages that provide virsh on Debian GNU/Linux 99?'" in hint


def test_dependency_hint_unknown_os(tmp_path: Path) -> None:
    hint = installer_deps.dependency_hint(["virsh"], tmp_path)
    assert hint.startswith("this OS (unknown) is not a known Debian/Ubuntu release")
    assert "'How do I install the packages that provide virsh on my operating system?'" in hint


def test_os_release_label_variants() -> None:
    # pretty_name that already carries the version is used verbatim.
    assert installer_deps.os_release_label(DEBIAN12) == "Debian GNU/Linux 12 (bookworm)"
    # pretty_name without the numeric version gets it appended.
    no_version = OsRelease("debian", "99", "Debian GNU/Linux forky/sid")
    assert installer_deps.os_release_label(no_version) == "Debian GNU/Linux forky/sid (version 99)"
    # No pretty_name falls back to id + version; nothing at all -> unknown.
    assert installer_deps.os_release_label(OsRelease("debian", "99", "")) == "debian 99"
    assert installer_deps.os_release_label(OsRelease("debian", "", "")) == "debian"
    assert installer_deps.os_release_label(MACOS) == "unknown"


def test_search_prompt_names_deps_and_version() -> None:
    prompt = installer_deps.search_prompt(DEBIAN99, ["virsh", "nbdcopy"])
    assert prompt == ("How do I install the packages that provide virsh, nbdcopy on Debian GNU/Linux 99 (futuristic)?")
    assert installer_deps.search_prompt(MACOS, ["virsh"]) == (
        "How do I install the packages that provide virsh on my operating system?"
    )


def test_required_binary_failures_appends_hints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_os_release(tmp_path, "ID=debian\nVERSION_ID=12\n")
    present = {"qemu-img", "df"}
    monkeypatch.setattr(installer_deps.shutil, "which", lambda name: f"/usr/bin/{name}" if name in present else None)
    monkeypatch.setattr(installer_deps, "_binary_runs", lambda _path: True)
    failures = installer_deps.required_binary_failures(["virsh", "qemu-img", "df", "nbdcopy", "kopia"], tmp_path)
    assert failures == [
        "missing binary: virsh",
        "missing binary: nbdcopy",
        "missing binary: kopia",
        "install the missing packages with: sudo apt-get update && sudo apt-get install -y libnbd-bin libvirt-clients",
        installer_deps.KOPIA_INSTALL_HINT,
    ]


def test_required_binary_failures_all_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer_deps.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(installer_deps, "_binary_runs", lambda _path: True)
    assert installer_deps.required_binary_failures(["virsh", "kopia"]) == []


# --- friendly error block ----------------------------------------------------


def test_dependency_error_lines_supported() -> None:
    text = "\n".join(installer_deps.dependency_error_lines(DEBIAN12, ["virsh", "nbdcopy"]))
    assert "Missing system dependencies: virsh (libvirt-clients), nbdcopy (libnbd-bin)" in text
    assert "Detected OS: Debian GNU/Linux 12 (bookworm)" in text
    assert "sudo apt-get update && sudo apt-get install -y libnbd-bin libvirt-clients" in text
    assert "never" in text and "consent" in text


def test_dependency_error_lines_newer_release() -> None:
    text = "\n".join(installer_deps.dependency_error_lines(DEBIAN99, ["virsh"]))
    assert "Detected OS: Debian GNU/Linux 99 (futuristic)" in text
    assert "Your debian version (99) is not one this" in text
    assert "sudo apt-get update && sudo apt-get install -y libvirt-clients" in text
    assert "copy-paste this" in text and "Google or ChatGPT" in text
    assert "  How do I install the packages that provide virsh on Debian GNU/Linux 99 (futuristic)?" in text


def test_dependency_error_lines_unknown_os() -> None:
    text = "\n".join(installer_deps.dependency_error_lines(MACOS, ["virsh"]))
    assert "Detected OS: unknown" in text
    assert "does not look like a Debian or Ubuntu system" in text
    assert "Google or ChatGPT" in text
    assert "  How do I install the packages that provide virsh on my operating system?" in text
    assert "apt-get install" not in text


# --- interactive plumbing ----------------------------------------------------


def test_stdin_is_tty_handles_valueerror(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenStdin:
        def isatty(self) -> bool:
            raise ValueError("stdin closed")

    monkeypatch.setattr(installer_deps.sys, "stdin", BrokenStdin())
    assert not installer_deps._stdin_is_tty()


def test_confirm_answers(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("builtins.input", lambda: " YES ")
    assert installer_deps._confirm("go? ")
    monkeypatch.setattr("builtins.input", lambda: "n")
    assert not installer_deps._confirm("go? ")

    def eof() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert not installer_deps._confirm("go? ")
    assert "go? " in capsys.readouterr().err


def test_run_visible_success_and_oserror() -> None:
    assert installer_deps._run_visible([sys.executable, "-c", "raise SystemExit(0)"]) == 0
    assert installer_deps._run_visible([sys.executable, "-c", "raise SystemExit(3)"]) == 3
    assert installer_deps._run_visible(["/nonexistent-binary-for-test"]) == 1


def _capture_run_visible(monkeypatch: pytest.MonkeyPatch, fail_on: str | None = None) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run_visible(args: list[str]) -> int:
        calls.append(args)
        return 1 if fail_on is not None and fail_on in " ".join(args) else 0

    monkeypatch.setattr(installer_deps, "_run_visible", fake_run_visible)
    return calls


def test_apt_install_as_root_skips_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run_visible(monkeypatch)
    assert installer_deps._apt_install(["libnbd-bin"], as_root=True, interactive=True)
    assert calls == [["apt-get", "update"], ["apt-get", "install", "-y", "libnbd-bin"]]


def test_apt_install_non_root_uses_and_drops_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run_visible(monkeypatch)
    assert installer_deps._apt_install(["libnbd-bin"], as_root=False, interactive=True)
    assert calls == [
        ["sudo", "-v"],
        ["sudo", "apt-get", "update"],
        ["sudo", "apt-get", "install", "-y", "libnbd-bin"],
        ["sudo", "-k"],
    ]


def test_apt_install_sudo_auth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run_visible(monkeypatch, fail_on="-v")
    assert not installer_deps._apt_install(["libnbd-bin"], as_root=False, interactive=True)
    assert calls == [["sudo", "-v"]]


def test_apt_install_failure_still_drops_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run_visible(monkeypatch, fail_on="update")
    assert not installer_deps._apt_install(["libnbd-bin"], as_root=False, interactive=True)
    # apt-get update failed, install is skipped, but sudo -k still runs.
    assert calls == [["sudo", "-v"], ["sudo", "apt-get", "update"], ["sudo", "-k"]]
