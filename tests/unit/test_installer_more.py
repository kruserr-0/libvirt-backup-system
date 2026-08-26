"""Overflow install tests kept here so the sibling files stay under 300 LOC."""

from __future__ import annotations

from pathlib import Path

import pytest

from libvirt_backup_system import installer_binaries
from libvirt_backup_system.installer import install
from libvirt_backup_system.installer_binaries import install_kopia
from tests.unit.conftest import stub_ensure_kopia_repo


def test_install_kopia_default_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    # Calling install_kopia() without a prefix MUST default to / so
    # production installs land in /usr/local/bin.
    seen: dict[str, Path] = {}

    def fake_kopia_probe(path: Path) -> str | None:
        seen["kopia"] = path
        return installer_binaries.KOPIA_VERSION

    monkeypatch.setattr("libvirt_backup_system.installer_binaries._kopia_installed_version", fake_kopia_probe)

    install_kopia()

    assert seen["kopia"] == Path("/usr/local/bin/kopia")


def test_install_returns_repo_setup_failure_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backup_path = tmp_path / "backups"
    backup_path.mkdir()
    machine_id = tmp_path / "etc/machine-id"
    machine_id.parent.mkdir(parents=True)
    machine_id.write_text("11111111111111111111111111111111\n", encoding="utf-8")
    monkeypatch.setenv("BACKUP_PATH", str(backup_path))
    monkeypatch.setattr("libvirt_backup_system.installer.Path.exists", Path.exists)
    monkeypatch.setattr("libvirt_backup_system.installer.preflight.repo_creation_failures", lambda _cfg: [])
    monkeypatch.setattr("libvirt_backup_system.installer_password.kopia_password.generate_password", lambda: "auto-pw")
    stub_ensure_kopia_repo(monkeypatch, return_code=5)

    assert install(str(tmp_path)) == 5
