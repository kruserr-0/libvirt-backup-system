"""CLI dispatch tests for the ``temp-restore`` subcommand tree."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from libvirt_backup_system.cli import main
from libvirt_backup_system.config import DEFAULTS, Config

from .conftest import ALPHA_UUID

TIMESTAMP = "20260507T101112"


def _fake_config(tmp_path: Path) -> Config:
    return Config(values=dict(DEFAULTS), path=tmp_path / "config.env", prefix=tmp_path)


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    cfg = _fake_config(tmp_path)
    monkeypatch.setattr("libvirt_backup_system.cli.Config.load", lambda config_path=None, prefix=None: cfg)
    monkeypatch.setattr("libvirt_backup_system.cli_restore.validate_config", lambda config: 0)
    return cfg


def test_temp_restore_requires_a_subcommand(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["temp-restore"])
    assert excinfo.value.code == 2
    assert "restore" in capsys.readouterr().err


def test_temp_restore_reports_validate_config(cli_env: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("libvirt_backup_system.cli_restore.validate_config", lambda config: 7)
    monkeypatch.setattr(
        "libvirt_backup_system.cli_restore.list_temp_restores",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not run when validate fails")),
    )
    assert main(["temp-restore", "list"]) == 7


def test_temp_restore_restore_forwards_arguments_under_lock(
    cli_env: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    lock_states: list[str] = []

    @contextlib.contextmanager
    def fake_lock(config: object):
        assert config is cli_env
        lock_states.append("locked")
        yield Path("/tmp/fake.lock")
        lock_states.append("released")

    def fake_restore_temp(config: object, vm_uuid: str, timestamp: str, **kwargs: object) -> int:
        assert lock_states == ["locked"]
        captured.update({"config": config, "vm_uuid": vm_uuid, "timestamp": timestamp, **kwargs})
        return 0

    monkeypatch.setattr("libvirt_backup_system.cli_restore.acquire_run_lock", fake_lock)
    monkeypatch.setattr("libvirt_backup_system.cli_restore.restore_temp", fake_restore_temp)
    argv = ["temp-restore", "restore", "-v", "--host-id", "host-b", "--run-id", "run-2", ALPHA_UUID, TIMESTAMP]
    assert main(argv) == 0
    assert captured == {
        "config": cli_env,
        "vm_uuid": ALPHA_UUID,
        "timestamp": TIMESTAMP,
        "host_id": "host-b",
        "run_id": "run-2",
        "verbose": True,
    }
    assert lock_states == ["locked", "released"]


def test_temp_restore_restore_reports_lock_busy(
    cli_env: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from libvirt_backup_system.lock import LockBusyError

    @contextlib.contextmanager
    def busy(config: object):
        raise LockBusyError(tmp_path / "run.lock")
        yield  # pragma: no cover

    monkeypatch.setattr("libvirt_backup_system.cli_restore.acquire_run_lock", busy)
    monkeypatch.setattr(
        "libvirt_backup_system.cli_restore.restore_temp",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not run while lock is busy")),
    )
    assert main(["temp-restore", "restore", ALPHA_UUID, TIMESTAMP]) == 1
    assert "another run in progress" in capsys.readouterr().err


def test_temp_restore_list_dispatches_with_json_flag(cli_env: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_list(config: object, *, json_output: bool) -> int:
        captured.update({"config": config, "json_output": json_output})
        return 0

    monkeypatch.setattr("libvirt_backup_system.cli_restore.list_temp_restores", fake_list)
    assert main(["temp-restore", "list", "--json"]) == 0
    assert captured == {"config": cli_env, "json_output": True}
    assert main(["temp-restore", "list"]) == 0
    assert captured["json_output"] is False


def test_temp_restore_stop_and_remove_dispatch(cli_env: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "libvirt_backup_system.cli_restore.stop_temp_restore",
        lambda config, name: calls.append(("stop", name)) or 3,
    )
    monkeypatch.setattr(
        "libvirt_backup_system.cli_restore.remove_temp_restore",
        lambda config, name: calls.append(("remove", name)) or 4,
    )
    assert main(["temp-restore", "stop", "myvm-temp-20260101T010101"]) == 3
    assert main(["temp-restore", "remove", "myvm-temp-20260101T010101"]) == 4
    assert calls == [("stop", "myvm-temp-20260101T010101"), ("remove", "myvm-temp-20260101T010101")]
