"""Orchestration tests for ``temp_restore.restore_temp``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from libvirt_backup_system import temp_restore
from libvirt_backup_system.shell import CommandError, CommandResult
from libvirt_backup_system.temp_restore_state import RECORD_FILENAME, read_record, temp_restore_root
from libvirt_backup_system.vms import is_safe_vm_uuid

from .conftest import ALPHA_UUID
from .restore_helpers import TIMESTAMP, make_config, make_manifest, make_row, ok_result

TEMP_NAME = f"myvm-temp-{TIMESTAMP}"


def _no_domain_run(args: list[str], **_: Any) -> CommandResult:
    if "domuuid" in args:
        raise CommandError(CommandResult(args, 1, "", "no domain"))
    return ok_result(args)


def _wire_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> dict[str, Any]:
    """Stub every collaborator for a successful temp restore; overrides win."""
    captured: dict[str, Any] = {"defines": [], "starts": []}
    manifest = overrides.pop("manifest", make_manifest())
    row = make_row(tmp_path)

    def stream(_c: Any, _r: Any, _s: str, _f: str, dest: Path) -> bool:
        dest.write_bytes(b"disk")
        return True

    def define(_c: Any, xml_path: Path, vm_uuid: str, name: str | None) -> bool:
        captured["defines"].append((xml_path, vm_uuid, name))
        return True

    def power(_c: Any, name: str, state: str, *, runner: Any = None) -> bool:
        captured["starts"].append((name, state))
        return True

    stubs: dict[str, Any] = {
        "match_row": lambda *_a, **_k: row,
        "restore_manifest": lambda *_a, **_k: manifest,
        "manifest_matches_request": lambda *_a, **_k: True,
        "disk_snapshot_id": lambda *_a, **_k: "snap-1",
        "stream_disk_to_qcow2": stream,
        "define_restored_domain": define,
        "restore_vm_power": power,
        "run": _no_domain_run,
    }
    stubs.update(overrides)
    for name, stub in stubs.items():
        monkeypatch.setattr(temp_restore, name, stub)
    return captured


def _staging(tmp_path: Path) -> Path:
    cfg = make_config(tmp_path)
    return temp_restore_root(cfg) / f"{ALPHA_UUID}-{TIMESTAMP}"


def test_temp_restore_success_defines_and_starts_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured = _wire_happy_path(tmp_path, monkeypatch)
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 0
    staging = _staging(tmp_path)
    assert (staging / "vda.qcow2").read_bytes() == b"disk"
    record = read_record(staging / RECORD_FILENAME)
    assert record.temp_name == TEMP_NAME
    assert record.source_vm_uuid == ALPHA_UUID
    assert is_safe_vm_uuid(record.temp_uuid)
    assert record.temp_uuid != ALPHA_UUID
    assert record.disks == (str(staging / "vda.qcow2"),)
    (xml_path, defined_uuid, defined_name) = captured["defines"][0]
    assert xml_path == staging / temp_restore.TEMP_CONFIG_FILE
    assert defined_uuid == record.temp_uuid
    assert defined_name == TEMP_NAME
    assert captured["starts"] == [(TEMP_NAME, "running")]
    out = capsys.readouterr().out
    assert "temp restore completed" in out
    assert "restored temp disk" in out


def test_temp_restore_quiet_without_verbose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _wire_happy_path(tmp_path, monkeypatch)
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP, verbose=False) == 0
    assert "restored temp disk" not in capsys.readouterr().out


def test_temp_restore_rejects_invalid_uuid(tmp_path: Path) -> None:
    assert temp_restore.restore_temp(make_config(tmp_path), "not-a-uuid", TIMESTAMP) == 1


@pytest.mark.parametrize("timestamp", ["..", "nope", "2026-01-01T00:00:00"])
def test_temp_restore_rejects_malformed_timestamp(tmp_path: Path, timestamp: str) -> None:
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, timestamp) == 1


def test_temp_restore_fails_when_backup_path_not_mount(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.values["BACKUP_REQUIRE_NFS_MOUNT"] = "true"
    assert temp_restore.restore_temp(cfg, ALPHA_UUID, TIMESTAMP) == 1


def test_temp_restore_fails_without_matching_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(temp_restore, "match_row", lambda *_a, **_k: None)
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1


def test_temp_restore_fails_when_root_cannot_be_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_happy_path(tmp_path, monkeypatch)
    root = _staging(tmp_path).parent
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text("", encoding="utf-8")  # a file where the root dir must go
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1


def test_temp_restore_rejects_unsafe_staging_subpath(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_happy_path(tmp_path, monkeypatch)
    monkeypatch.setattr(temp_restore, "subpath_is_safe", lambda _r, _p: False)
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1


def test_temp_restore_refuses_existing_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _wire_happy_path(tmp_path, monkeypatch)
    staging = _staging(tmp_path)
    staging.mkdir(parents=True)
    keep = staging / "keep.txt"
    keep.write_text("clone data", encoding="utf-8")
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1
    # An existing clone dir may back a *running* VM: it must never be deleted.
    assert keep.read_text(encoding="utf-8") == "clone data"
    assert "already exists" in capsys.readouterr().err


def test_temp_restore_fails_when_staging_mkdir_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_happy_path(tmp_path, monkeypatch)
    real_mkdir = Path.mkdir

    def boom(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name.startswith(ALPHA_UUID):
            raise OSError("no space")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", boom)
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"restore_manifest": lambda *_a, **_k: None},
        {"manifest_matches_request": lambda *_a, **_k: False},
        {"disk_snapshot_id": lambda *_a, **_k: None},
        {"stream_disk_to_qcow2": lambda *_a, **_k: False},
        {"rewrite_for_temp": lambda *_a, **_k: None},
        {"define_restored_domain": lambda *_a, **_k: False},
    ],
    ids=["manifest", "mismatch", "snapshot-id", "stream", "rewrite", "define"],
)
def test_temp_restore_cleans_staging_on_pre_start_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, override: dict[str, Any]
) -> None:
    _wire_happy_path(tmp_path, monkeypatch, **override)
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1
    assert not _staging(tmp_path).exists()


def test_temp_restore_cleans_staging_on_unsafe_manifest_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _wire_happy_path(tmp_path, monkeypatch, manifest=make_manifest(vm_name="-evil"))
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1
    assert not _staging(tmp_path).exists()
    assert "manifest carries unsafe vm name" in capsys.readouterr().err


def test_temp_restore_cleans_staging_when_record_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_happy_path(tmp_path, monkeypatch)
    monkeypatch.setattr(temp_restore.TempRestoreRecord, "write", lambda _self, _directory: False)
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1
    assert not _staging(tmp_path).exists()


def test_temp_restore_refuses_when_clone_name_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _wire_happy_path(tmp_path, monkeypatch, run=lambda args, **_: ok_result(args, "some-uuid\n"))
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1
    assert not _staging(tmp_path).exists()
    assert "already exists" in capsys.readouterr().err


def test_temp_restore_fails_when_domain_check_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_virsh(args: list[str], **_: Any) -> CommandResult:
        raise OSError("virsh missing")

    _wire_happy_path(tmp_path, monkeypatch, run=no_virsh)
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1
    assert not _staging(tmp_path).exists()
    assert "virsh domuuid unavailable" in capsys.readouterr().err


def test_temp_restore_keeps_staging_when_start_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _wire_happy_path(tmp_path, monkeypatch, restore_vm_power=lambda *_a, **_k: False)
    assert temp_restore.restore_temp(make_config(tmp_path), ALPHA_UUID, TIMESTAMP) == 1
    staging = _staging(tmp_path)
    # Defined but not started: the clone exists in libvirt, so its files must
    # survive for ``temp-restore remove`` to clean up properly.
    assert (staging / RECORD_FILENAME).is_file()
    assert "temp restore VM defined but failed to start" in capsys.readouterr().err
