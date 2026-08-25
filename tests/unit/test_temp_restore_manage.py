"""Tests for temp-restore list/stop/remove management commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from libvirt_backup_system import temp_restore_manage as manage
from libvirt_backup_system.config import Config
from libvirt_backup_system.shell import CommandError, CommandResult
from libvirt_backup_system.temp_restore_state import TempRestoreRecord, temp_restore_root

from .conftest import ALPHA_UUID, BETA_UUID
from .restore_helpers import TIMESTAMP, make_config, ok_result

TEMP_NAME = f"myvm-temp-{TIMESTAMP}"


def seed_record(cfg: Config, *, temp_name: str = TEMP_NAME, staging: Path | None = None) -> TempRestoreRecord:
    directory = temp_restore_root(cfg) / f"{ALPHA_UUID}-{TIMESTAMP}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "vda.qcow2").write_bytes(b"disk")
    record = TempRestoreRecord(
        temp_name=temp_name,
        temp_uuid=BETA_UUID,
        source_vm_name="myvm",
        source_vm_uuid=ALPHA_UUID,
        timestamp=TIMESTAMP,
        host_id="host-a",
        run_id="run-1",
        created_at="2026-01-02T03:04:05Z",
        staging=str(staging if staging is not None else directory),
        disks=(str(directory / "vda.qcow2"),),
    )
    assert record.write(directory) is True
    return record


def _virsh_run(monkeypatch: pytest.MonkeyPatch, side: dict[str, Any]) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: Any) -> CommandResult:
        calls.append(args)
        for verb, effect in side.items():
            if verb in args:
                if isinstance(effect, BaseException):
                    raise effect
                return effect(args)
        return ok_result(args)

    monkeypatch.setattr(manage, "run", fake_run)
    return calls


def test_list_without_records_logs_info(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert manage.list_temp_restores(make_config(tmp_path)) == 0
    assert "no temp restore VMs found" in capsys.readouterr().out


def test_list_without_records_emits_empty_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert manage.list_temp_restores(make_config(tmp_path), json_output=True) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_list_renders_table_with_domain_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_config(tmp_path)
    seed_record(cfg)
    _virsh_run(monkeypatch, {"domstate": lambda args: ok_result(args, "running\n")})
    assert manage.list_temp_restores(cfg) == 0
    out = capsys.readouterr().out
    assert "temp-name" in out
    assert TEMP_NAME in out
    assert "running" in out


def test_list_reports_missing_when_virsh_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_config(tmp_path)
    record = seed_record(cfg)
    _virsh_run(monkeypatch, {"domstate": CommandError(CommandResult(["virsh"], 1, "", "gone"))})
    assert manage.list_temp_restores(cfg, json_output=True) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows == [
        {
            "created_at": record.created_at,
            "source_host_id": "host-a",
            "source_vm_name": "myvm",
            "source_vm_uuid": ALPHA_UUID,
            "staging": record.staging,
            "state": "missing",
            "temp_name": TEMP_NAME,
            "temp_uuid": BETA_UUID,
            "timestamp": TIMESTAMP,
        }
    ]


@pytest.mark.parametrize("command", [manage.stop_temp_restore, manage.remove_temp_restore])
def test_manage_rejects_unsafe_name(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: Any) -> None:
    assert command(make_config(tmp_path), "-bad/name") == 1
    assert "not a valid VM name" in capsys.readouterr().err


@pytest.mark.parametrize("command", [manage.stop_temp_restore, manage.remove_temp_restore])
def test_manage_rejects_unknown_name(tmp_path: Path, capsys: pytest.CaptureFixture[str], command: Any) -> None:
    assert command(make_config(tmp_path), "not-a-clone") == 1
    assert "no temp restore VM with that name" in capsys.readouterr().err


def test_stop_destroys_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_config(tmp_path)
    seed_record(cfg)
    calls = _virsh_run(monkeypatch, {"domstate": lambda args: ok_result(args, "shut off\n")})
    assert manage.stop_temp_restore(cfg, TEMP_NAME) == 0
    assert any("destroy" in args for args in calls)
    assert "temp restore VM stopped" in capsys.readouterr().out


def test_stop_tolerates_already_stopped_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_config(tmp_path)
    seed_record(cfg)
    _virsh_run(monkeypatch, {"destroy": CommandError(CommandResult(["virsh"], 1, "", "not running"))})
    assert manage.stop_temp_restore(cfg, TEMP_NAME) == 0
    assert "likely already off" in capsys.readouterr().out


def test_stop_fails_when_virsh_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_config(tmp_path)
    seed_record(cfg)
    _virsh_run(monkeypatch, {"destroy": OSError("no virsh")})
    assert manage.stop_temp_restore(cfg, TEMP_NAME) == 1


def test_remove_stops_undefines_and_deletes_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_config(tmp_path)
    record = seed_record(cfg)
    calls = _virsh_run(monkeypatch, {"domuuid": lambda args: ok_result(args, f"{BETA_UUID}\n")})
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 0
    flat = {token for args in calls for token in args}
    assert {"destroy", "undefine", "--nvram"}.issubset(flat)
    assert not Path(record.staging).exists()
    assert "temp restore VM removed" in capsys.readouterr().out


def test_remove_skips_virsh_when_domain_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_config(tmp_path)
    record = seed_record(cfg)
    calls = _virsh_run(monkeypatch, {"domuuid": CommandError(CommandResult(["virsh"], 1, "", "no domain"))})
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 0
    assert not any("destroy" in args or "undefine" in args for args in calls)
    assert not Path(record.staging).exists()


def test_remove_treats_blank_domuuid_as_absent_domain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_config(tmp_path)
    record = seed_record(cfg)
    calls = _virsh_run(monkeypatch, {"domuuid": lambda args: ok_result(args, "  \n")})
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 0
    assert not any("destroy" in args for args in calls)
    assert not Path(record.staging).exists()


def test_remove_refuses_when_domain_uuid_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_config(tmp_path)
    record = seed_record(cfg)
    calls = _virsh_run(monkeypatch, {"domuuid": lambda args: ok_result(args, f"{ALPHA_UUID}\n")})
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 1
    assert not any("destroy" in args or "undefine" in args for args in calls)
    assert Path(record.staging).exists()
    assert "refusing to undefine" in capsys.readouterr().err


def test_remove_fails_when_domuuid_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_config(tmp_path)
    record = seed_record(cfg)
    _virsh_run(monkeypatch, {"domuuid": OSError("no virsh")})
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 1
    assert Path(record.staging).exists()


def test_remove_fails_when_destroy_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_config(tmp_path)
    seed_record(cfg)
    _virsh_run(
        monkeypatch,
        {"domuuid": lambda args: ok_result(args, f"{BETA_UUID}\n"), "destroy": OSError("no virsh")},
    )
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 1


@pytest.mark.parametrize(
    "effect",
    [CommandError(CommandResult(["virsh"], 1, "", "boom")), OSError("no virsh")],
    ids=["command-error", "os-error"],
)
def test_remove_fails_when_undefine_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, effect: BaseException
) -> None:
    cfg = make_config(tmp_path)
    record = seed_record(cfg)
    _virsh_run(monkeypatch, {"domuuid": lambda args: ok_result(args, f"{BETA_UUID}\n"), "undefine": effect})
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 1
    assert Path(record.staging).exists()


def test_remove_refuses_staging_outside_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_config(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    seed_record(cfg, staging=elsewhere)
    _virsh_run(monkeypatch, {"domuuid": CommandError(CommandResult(["virsh"], 1, "", "no domain"))})
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 1
    assert elsewhere.exists()
    assert "outside the state dir" in capsys.readouterr().err


def test_remove_tolerates_missing_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_config(tmp_path)
    gone = temp_restore_root(cfg) / "already-gone"
    record = seed_record(cfg, staging=gone)
    _virsh_run(monkeypatch, {"domuuid": CommandError(CommandResult(["virsh"], 1, "", "no domain"))})
    assert not Path(record.staging).exists()
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 0


def test_remove_fails_when_staging_removal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = make_config(tmp_path)
    seed_record(cfg)
    _virsh_run(monkeypatch, {"domuuid": CommandError(CommandResult(["virsh"], 1, "", "no domain"))})

    def refuse(_path: Any) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(manage.shutil, "rmtree", refuse)
    assert manage.remove_temp_restore(cfg, TEMP_NAME) == 1
    assert "staging removal failed" in capsys.readouterr().err
