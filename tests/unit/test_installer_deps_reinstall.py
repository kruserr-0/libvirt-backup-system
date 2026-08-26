"""Tests for ``install --reinstall-deps`` (forced dependency reinstall)."""

from __future__ import annotations

from pathlib import Path

import pytest

from libvirt_backup_system import installer_deps
from libvirt_backup_system.installer_deps import ensure_system_deps
from tests.unit.test_installer_deps import _capture_run_visible
from tests.unit.test_installer_deps_gate import DEBIAN12, UNKNOWN, _set_os, _stub_missing


def test_reinstall_targets_all_deps_even_when_none_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_missing(monkeypatch, [])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(installer_deps, "_confirm", lambda prompt: True)
    installed: list[tuple[list[str], bool]] = []

    def fake_apt(packages: list[str], *, as_root: bool, interactive: bool, reinstall: bool = False) -> bool:
        installed.append((packages, reinstall))
        return True

    monkeypatch.setattr(installer_deps, "_apt_install", fake_apt)
    assert ensure_system_deps(Path("/"), reinstall=True) == 0
    assert installed == [(["libnbd-bin", "libvirt-clients", "qemu-utils"], True)]
    out = capsys.readouterr()
    assert "Reinstalling system dependencies" in out.err
    assert "reinstalled system dependencies" in out.out


def test_reinstall_prompts_with_reinstall_wording(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_missing(monkeypatch, [])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    prompts: list[str] = []

    def decline(prompt: str) -> bool:
        prompts.append(prompt)
        return False

    monkeypatch.setattr(installer_deps, "_confirm", decline)
    assert ensure_system_deps(Path("/"), reinstall=True) == 1
    assert prompts == ["Reinstall these packages now via apt-get? [y/N] "]


def test_reinstall_with_missing_deps_still_covers_all(monkeypatch: pytest.MonkeyPatch) -> None:
    # A broken host with one missing binary: --reinstall-deps reinstalls the
    # whole set, not just the missing one.
    _stub_missing(monkeypatch, ["nbdcopy"], [])
    _set_os(monkeypatch, DEBIAN12)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(installer_deps, "_confirm", lambda prompt: True)
    installed: list[list[str]] = []

    def fake_apt(packages: list[str], *, as_root: bool, interactive: bool, reinstall: bool = False) -> bool:
        installed.append(packages)
        return True

    monkeypatch.setattr(installer_deps, "_apt_install", fake_apt)
    assert ensure_system_deps(Path("/"), reinstall=True) == 0
    assert installed == [["libnbd-bin", "libvirt-clients", "qemu-utils"]]


def test_reinstall_on_unknown_release_fails_with_friendly_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_missing(monkeypatch, [])
    _set_os(monkeypatch, UNKNOWN)
    monkeypatch.setattr(installer_deps, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(
        installer_deps, "_apt_install", lambda packages, **kwargs: pytest.fail("unknown OS never installs")
    )
    assert ensure_system_deps(Path("/"), reinstall=True) == 1
    assert "does not look like a Debian or Ubuntu system" in capsys.readouterr().err


def test_apt_install_reinstall_passes_apt_reinstall_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture_run_visible(monkeypatch)
    assert installer_deps._apt_install(["libnbd-bin"], as_root=True, interactive=True, reinstall=True)
    assert calls == [
        ["apt-get", "update"],
        ["apt-get", "install", "-y", "--reinstall", "libnbd-bin"],
    ]
