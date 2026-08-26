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


def test_install_kopia_force_skips_idempotency_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # --reinstall-deps: even a binary that reports the pinned version is
    # re-extracted, repairing a copy that is broken in some other way.
    import hashlib
    import io
    import tarfile

    from libvirt_backup_system.kopia_vendor import KOPIA_TAR_ROOT

    kopia_path = tmp_path / "usr/local/bin/kopia"
    kopia_path.parent.mkdir(parents=True)
    kopia_path.write_text("broken but version-correct\n", encoding="utf-8")

    def probe_must_not_run(path: Path) -> str | None:
        raise AssertionError("force must skip the version probe")

    monkeypatch.setattr("libvirt_backup_system.installer_binaries._kopia_installed_version", probe_must_not_run)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        body = b"#!/bin/sh\necho kopia\n"
        info = tarfile.TarInfo(f"{KOPIA_TAR_ROOT}/kopia")
        info.size = len(body)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(body))
    payload = buf.getvalue()
    monkeypatch.setattr(installer_binaries, "vendored_kopia_tarball_bytes", lambda: payload)
    monkeypatch.setattr(installer_binaries, "KOPIA_SHA256", hashlib.sha256(payload).hexdigest())

    install_kopia(prefix=tmp_path, force=True)

    assert kopia_path.read_text(encoding="utf-8").startswith("#!/bin/sh")


def test_install_forwards_reinstall_deps_to_gate_and_kopia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, bool] = {}

    def fake_ensure(
        root: Path, *, non_interactive: bool = False, assume_yes: bool = False, reinstall: bool = False
    ) -> int:
        seen["reinstall"] = reinstall
        return 0

    def fake_install_kopia(prefix: Path | None = None, *, force: bool = False) -> None:
        seen["kopia_force"] = force

    from tests.unit.conftest import write_kopia_password_file

    write_kopia_password_file(tmp_path)
    monkeypatch.setattr("libvirt_backup_system.installer.Path.exists", Path.exists)
    monkeypatch.setattr("libvirt_backup_system.installer.installer_deps.ensure_system_deps", fake_ensure)
    monkeypatch.setattr("libvirt_backup_system.installer.install_kopia", fake_install_kopia)

    assert install(str(tmp_path), reinstall_deps=True) == 0

    assert seen == {"reinstall": True, "kopia_force": True}
