"""Tests for ``installer_deps.ensure_system_deps`` and its wiring into install."""

from __future__ import annotations

from pathlib import Path

import pytest

from libvirt_backup_system import installer_deps
from libvirt_backup_system.cli import main
from libvirt_backup_system.installer_deps import ensure_system_deps


def _stub_missing(monkeypatch: pytest.MonkeyPatch, *sequences: list[str]) -> list[int]:
    """Make missing_system_deps return each sequence in order (last repeats)."""
    calls = [0]
    seqs = list(sequences)

    def fake_missing() -> list[str]:
        index = min(calls[0], len(seqs) - 1)
        calls[0] += 1
        return seqs[index]

    monkeypatch.setattr(installer_deps, "missing_system_deps", fake_missing)
    return calls


def _set_os(monkeypatch: pytest.MonkeyPatch, os_release: installer_deps.OsRelease) -> None:
    monkeypatch.setattr(installer_deps, "detect_os", lambda prefix=None: os_release)


DEBIAN12 = installer_deps.OsRelease("debian", "12", "Debian GNU/Linux 12 (bookworm)")
UNKNOWN = installer_deps.OsRelease("", "", "")


def test_sandboxed_prefix_skips_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert ensure_system_deps(tmp_path) == 0
    assert "system dependency check skipped for sandboxed prefix" in capsys.readouterr().out


def test_no_missing_deps_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_missing(monkeypatch, [])
    assert ensure_system_deps(Path("/")) == 0


def test_missing_deps_non_interactive_fails_with_copy_paste(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_missing(monkeypatch, ["nbdcopy"])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    assert ensure_system_deps(Path("/"), non_interactive=True) == 1
    err = capsys.readouterr().err
    assert "sudo apt-get update && sudo apt-get install -y libnbd-bin" in err
    assert "Nothing was installed" in err


def test_missing_deps_non_tty_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _stub_missing(monkeypatch, ["virsh"])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: False)
    assert ensure_system_deps(Path("/")) == 1
    assert "libvirt-clients" in capsys.readouterr().err


def test_missing_deps_unknown_release_never_prompts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_missing(monkeypatch, ["virsh"])
    _set_os(monkeypatch, UNKNOWN)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(installer_deps, "_confirm", lambda prompt: pytest.fail("must not prompt on unknown OS"))
    assert ensure_system_deps(Path("/")) == 1
    assert "does not look like a Debian or Ubuntu system" in capsys.readouterr().err


def test_missing_deps_interactive_declined(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _stub_missing(monkeypatch, ["nbdcopy"])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(installer_deps, "_confirm", lambda prompt: False)
    monkeypatch.setattr(
        installer_deps,
        "_apt_install",
        lambda packages, **kwargs: pytest.fail("declined must not install"),
    )
    assert ensure_system_deps(Path("/")) == 1
    assert "Install them with:" in capsys.readouterr().err


def test_missing_deps_interactive_apt_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_missing(monkeypatch, ["nbdcopy"])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(installer_deps, "_confirm", lambda prompt: True)
    monkeypatch.setattr(installer_deps, "_apt_install", lambda packages, **kwargs: False)
    assert ensure_system_deps(Path("/")) == 1


def test_missing_deps_interactive_still_missing_after_apt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_missing(monkeypatch, ["nbdcopy"], ["nbdcopy"])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(installer_deps, "_confirm", lambda prompt: True)
    monkeypatch.setattr(installer_deps, "_apt_install", lambda packages, **kwargs: True)
    assert ensure_system_deps(Path("/")) == 1
    assert "still missing after apt-get install" in capsys.readouterr().err


def test_missing_deps_interactive_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _stub_missing(monkeypatch, ["nbdcopy", "virsh"], [])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(installer_deps, "_confirm", lambda prompt: True)
    installed: list[tuple[list[str], bool]] = []

    def fake_apt(packages: list[str], *, as_root: bool, interactive: bool, reinstall: bool = False) -> bool:
        installed.append((packages, as_root, interactive))
        return True

    monkeypatch.setattr(installer_deps, "_apt_install", fake_apt)
    assert ensure_system_deps(Path("/")) == 0
    assert installed == [(["libnbd-bin", "libvirt-clients"], installer_deps.os.geteuid() == 0, True)]
    assert "installed system dependencies" in capsys.readouterr().out


def test_missing_deps_assume_yes_skips_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_missing(monkeypatch, ["nbdcopy"], [])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(installer_deps, "_confirm", lambda prompt: pytest.fail("-y must not prompt"))
    monkeypatch.setattr(installer_deps, "_apt_install", lambda packages, **kwargs: True)
    assert ensure_system_deps(Path("/"), assume_yes=True) == 0
    assert "installed system dependencies" in capsys.readouterr().out


def test_missing_deps_non_interactive_with_yes_attempts_install(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_missing(monkeypatch, ["nbdcopy"], [])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(installer_deps, "_confirm", lambda prompt: pytest.fail("non-interactive must not prompt"))
    installed: list[tuple[list[str], bool]] = []

    def fake_apt(packages: list[str], *, as_root: bool, interactive: bool, reinstall: bool = False) -> bool:
        installed.append((packages, interactive))
        return True

    monkeypatch.setattr(installer_deps, "_apt_install", fake_apt)
    assert ensure_system_deps(Path("/"), non_interactive=True, assume_yes=True) == 0
    # The attempt runs in non-interactive mode (sudo -n, DEBIAN_FRONTEND).
    assert installed == [(["libnbd-bin"], False)]


def test_missing_deps_non_interactive_with_yes_skipped_apt_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # _apt_install returning False models "no passwordless sudo available":
    # the attempt is auto-skipped and the friendly error is printed instead.
    _stub_missing(monkeypatch, ["nbdcopy"])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(installer_deps, "_apt_install", lambda packages, **kwargs: False)
    assert ensure_system_deps(Path("/"), non_interactive=True, assume_yes=True) == 1
    assert "sudo apt-get update && sudo apt-get install -y libnbd-bin" in capsys.readouterr().err


def test_missing_deps_yes_on_unknown_release_still_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_missing(monkeypatch, ["virsh"])
    _set_os(monkeypatch, UNKNOWN)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: False)
    monkeypatch.setattr(
        installer_deps,
        "_apt_install",
        lambda packages, **kwargs: pytest.fail("unknown OS never installs"),
    )
    assert ensure_system_deps(Path("/"), non_interactive=True, assume_yes=True) == 1
    assert "does not look like a Debian or Ubuntu system" in capsys.readouterr().err


def test_cli_install_forwards_non_interactive_and_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, bool] = {}

    def fake_install(
        prefix: str | None,
        *,
        config_path: str | None = None,
        password_spec: object | None = None,
        non_interactive: bool = False,
        assume_yes: bool = False,
        reinstall_deps: bool = False,
    ) -> int:
        captured["non_interactive"] = non_interactive
        captured["assume_yes"] = assume_yes
        captured["reinstall_deps"] = reinstall_deps
        return 0

    monkeypatch.setattr("libvirt_backup_system.cli.install", fake_install)
    assert main(["--prefix", str(tmp_path), "install", "--non-interactive", "-y"]) == 0
    assert captured == {"non_interactive": True, "assume_yes": True, "reinstall_deps": False}
    assert main(["--prefix", str(tmp_path), "install"]) == 0
    assert captured == {"non_interactive": False, "assume_yes": False, "reinstall_deps": False}
    assert main(["--prefix", str(tmp_path), "install", "--yes", "--reinstall-deps"]) == 0
    assert captured == {"non_interactive": False, "assume_yes": True, "reinstall_deps": True}


def test_apt_install_non_interactive_uses_passwordless_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_installer_deps import _capture_run_visible

    calls = _capture_run_visible(monkeypatch)
    assert installer_deps._apt_install(["libnbd-bin"], as_root=False, interactive=False)
    assert calls == [
        ["sudo", "-n", "-v"],
        ["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update"],
        ["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "libnbd-bin"],
        ["sudo", "-k"],
    ]


def test_apt_install_non_interactive_skips_without_passwordless_sudo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.unit.test_installer_deps import _capture_run_visible

    calls = _capture_run_visible(monkeypatch, fail_on="-n -v")
    assert not installer_deps._apt_install(["libnbd-bin"], as_root=False, interactive=False)
    # Only the credential probe ran; the attempt is skipped, not errored.
    assert calls == [["sudo", "-n", "-v"]]
    assert "skipping automatic dependency install" in capsys.readouterr().out


def test_apt_install_non_interactive_as_root_skips_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_installer_deps import _capture_run_visible

    calls = _capture_run_visible(monkeypatch)
    assert installer_deps._apt_install(["libnbd-bin"], as_root=True, interactive=False)
    assert calls == [
        ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update"],
        ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "libnbd-bin"],
    ]


def test_install_aborts_before_mutating_when_deps_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from libvirt_backup_system.installer import install

    monkeypatch.setattr("libvirt_backup_system.installer.root_prefix", lambda prefix=None: Path("/"))
    monkeypatch.setattr(
        "libvirt_backup_system.installer.installer_deps.ensure_system_deps",
        lambda root, **kwargs: 1,
    )
    monkeypatch.setattr(
        "libvirt_backup_system.installer.Config.load",
        lambda *args, **kwargs: pytest.fail("install must abort before loading config"),
    )
    assert install(None) == 1
