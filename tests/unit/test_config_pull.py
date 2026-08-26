"""Tests for ``pull-config`` (config_sync.pull_local_config)."""

from __future__ import annotations

from pathlib import Path

import pytest

from libvirt_backup_system import config_sync
from libvirt_backup_system.cli import main
from libvirt_backup_system.config import Config


def _node(tmp_path: Path, *, local_text: str | None = None, seed_text: str | None = None) -> Config:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(exist_ok=True)
    config_path = tmp_path / "etc/libvirt-backup-system/libvirt-backup.env"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if local_text is not None:
        config_path.write_text(local_text.format(backup=backup_dir), encoding="utf-8")
    if seed_text is not None:
        (backup_dir / "libvirt-backup.env").write_text(seed_text, encoding="utf-8")
    return Config.load(config_path=str(config_path), prefix=str(tmp_path), apply_env_overrides=False)


def test_pull_takes_over_shared_config_preserving_identity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _node(
        tmp_path,
        local_text="BACKUP_PATH={backup}\nHOST_ID=node-b\nKEEP_DAILY=7\n",
        seed_text="KEEP_DAILY=365\nKOPIA_COMPRESSION=zstd-better\nSYSTEMD_ON_CALENDAR=*-*-* 04:00:00\nHOST_ID=\n",
    )

    assert config_sync.pull_local_config(cfg) == 0

    text = cfg.path.read_text(encoding="utf-8")
    # Shared settings taken over...
    assert "KEEP_DAILY=365" in text
    assert "KOPIA_COMPRESSION=zstd-better" in text
    assert "SYSTEMD_ON_CALENDAR=*-*-* 04:00:00" in text
    # ...while identity and location stay this host's own.
    assert "HOST_ID=node-b" in text
    assert f"BACKUP_PATH={tmp_path / 'backups'}" in text
    assert cfg.path.stat().st_mode & 0o777 == 0o600
    out = capsys.readouterr().out
    assert "pulled shared config" in out
    assert "libvirt-backup-system start" in out


def test_pull_keeps_empty_host_id_following_machine_id(tmp_path: Path) -> None:
    cfg = _node(tmp_path, local_text="BACKUP_PATH={backup}\n", seed_text="KEEP_DAILY=30\n")

    assert config_sync.pull_local_config(cfg) == 0

    text = cfg.path.read_text(encoding="utf-8")
    # No local HOST_ID -> stays empty/commented, still following machine-id.
    assert "# HOST_ID=\n" in text
    assert "KEEP_DAILY=30" in text


def test_pull_errors_without_backup_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _node(tmp_path, local_text="BACKUP_PATH=\n")

    assert config_sync.pull_local_config(cfg) == 1
    assert "BACKUP_PATH is not configured" in capsys.readouterr().err


def test_pull_errors_without_local_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _node(tmp_path, seed_text="KEEP_DAILY=30\n")
    cfg.values["BACKUP_PATH"] = str(tmp_path / "backups")

    assert config_sync.pull_local_config(cfg) == 1
    assert "run install first" in capsys.readouterr().err


def test_pull_errors_without_shared_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _node(tmp_path, local_text="BACKUP_PATH={backup}\n")

    assert config_sync.pull_local_config(cfg) == 1
    assert "run push-config on a configured node first" in capsys.readouterr().err


def test_pull_errors_when_shared_config_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _node(tmp_path, local_text="BACKUP_PATH={backup}\n", seed_text="KEEP_DAILY=30\n")
    real_parse = config_sync.parse_env_file

    def refuse_seed(path: Path) -> dict[str, str]:
        if path.name == "libvirt-backup.env" and path.parent.name == "backups":
            raise PermissionError("denied")
        return real_parse(path)

    monkeypatch.setattr(config_sync, "parse_env_file", refuse_seed)
    assert config_sync.pull_local_config(cfg) == 1
    assert "shared config unreadable" in capsys.readouterr().err


def test_pull_errors_when_local_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _node(tmp_path, local_text="BACKUP_PATH={backup}\n", seed_text="KEEP_DAILY=30\n")

    def refuse(_dest: Path, _content: str) -> None:
        raise PermissionError("read-only /etc")

    monkeypatch.setattr(config_sync, "_atomic_write", refuse)
    assert config_sync.pull_local_config(cfg) == 1
    assert "failed to write local config" in capsys.readouterr().err


def test_cli_pull_config_round_trip_with_push(tmp_path: Path) -> None:
    # push on "node A" (the local config), wipe a setting locally, pull it back.
    _node(
        tmp_path,
        local_text="BACKUP_PATH={backup}\nHOST_ID=node-a\nSYSTEMD_ON_CALENDAR=*-*-* 05:05:00\n",
    )
    assert main(["--prefix", str(tmp_path), "push-config"]) == 0

    config_path = tmp_path / "etc/libvirt-backup-system/libvirt-backup.env"
    config_path.write_text(f"BACKUP_PATH={tmp_path / 'backups'}\nHOST_ID=node-b\n", encoding="utf-8")
    assert main(["--prefix", str(tmp_path), "pull-config"]) == 0

    text = config_path.read_text(encoding="utf-8")
    assert "SYSTEMD_ON_CALENDAR=*-*-* 05:05:00" in text
    assert "HOST_ID=node-b" in text


def test_push_pull_workflow_is_canonical_across_surfaces(tmp_path: Path) -> None:
    """The 4-step push/pull workflow reads identically everywhere it appears."""
    from libvirt_backup_system import cli_help

    model = "flow through the shared NFS tree with an explicit"
    steps = (
        "# 1. apply locally",
        "# 2. publish for the cluster",
        "# 3. take over the shared config",
        "# 4. apply locally",
    )
    surfaces = {
        "program epilog": cli_help.PROGRAM_EPILOG,
        "push-config help": cli_help.PUSH_CONFIG_DESCRIPTION,
        "pull-config help": cli_help.PULL_CONFIG_DESCRIPTION,
        "rendered env template": Config.load(prefix=str(tmp_path), apply_env_overrides=False).render_env(),
    }
    for name, text in surfaces.items():
        for step in steps:
            assert step in text, f"{name} is missing workflow step {step!r}"
    assert model in cli_help.PROGRAM_DESCRIPTION.replace("\n", " ")
    for name in ("push-config help", "pull-config help", "rendered env template"):
        assert model in surfaces[name].replace("\n", " "), f"{name} is missing the model statement"
