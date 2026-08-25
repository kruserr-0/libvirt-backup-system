"""Tests for the temp-restore on-disk record store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from libvirt_backup_system.temp_restore_state import (
    RECORD_FILENAME,
    TEMP_RESTORE_DIR,
    TempRestoreRecord,
    find_record,
    load_records,
    parse_record,
    read_record,
    temp_restore_root,
)

from .conftest import ALPHA_UUID, BETA_UUID
from .restore_helpers import TIMESTAMP, make_config


def make_record(staging: Path, *, temp_name: str = f"myvm-temp-{TIMESTAMP}") -> TempRestoreRecord:
    return TempRestoreRecord(
        temp_name=temp_name,
        temp_uuid=BETA_UUID,
        source_vm_name="myvm",
        source_vm_uuid=ALPHA_UUID,
        timestamp=TIMESTAMP,
        host_id="host-a",
        run_id="run-1",
        created_at="2026-01-02T03:04:05Z",
        staging=str(staging),
        disks=(str(staging / "vda.qcow2"),),
    )


def test_record_round_trips_through_disk(tmp_path: Path) -> None:
    record = make_record(tmp_path)
    assert record.write(tmp_path) is True
    assert read_record(tmp_path / RECORD_FILENAME) == record


def test_record_write_fails_in_missing_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    record = make_record(tmp_path)
    assert record.write(tmp_path / "does-not-exist") is False
    assert "temp restore record write failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    "payload,message",
    [
        ("[]", "not a JSON object"),
        ("{}", "disks field is not a list"),
        ('{"disks": [1]}', "disk entry is not a string"),
        ('{"disks": [], "temp_name": 7}', "field 'temp_name' must be a string"),
    ],
    ids=["not-object", "disks-not-list", "disk-not-string", "field-not-string"],
)
def test_parse_record_rejects_malformed_payloads(payload: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_record(payload)


def test_parse_record_requires_every_string_field(tmp_path: Path) -> None:
    data = json.loads(make_record(tmp_path).to_json())
    del data["created_at"]
    with pytest.raises(ValueError, match="field 'created_at' must be a string"):
        parse_record(json.dumps(data))


def test_temp_restore_root_is_prefixed(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    assert temp_restore_root(cfg) == tmp_path / str(TEMP_RESTORE_DIR).lstrip("/")


def test_load_records_returns_empty_without_root(tmp_path: Path) -> None:
    assert load_records(make_config(tmp_path)) == []


def test_load_records_skips_corrupt_and_recordless_entries(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = make_config(tmp_path)
    root = temp_restore_root(cfg)
    good = root / f"{ALPHA_UUID}-{TIMESTAMP}"
    good.mkdir(parents=True)
    record = make_record(good)
    assert record.write(good) is True
    corrupt = root / "corrupt-dir"
    corrupt.mkdir()
    (corrupt / RECORD_FILENAME).write_text("not json", encoding="utf-8")
    (root / "no-record-dir").mkdir()
    (root / "stray-file").write_text("", encoding="utf-8")

    assert load_records(cfg) == [record]
    assert "skipping unreadable temp restore record" in capsys.readouterr().err


def test_find_record_matches_by_temp_name(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    root = temp_restore_root(cfg)
    staging = root / f"{ALPHA_UUID}-{TIMESTAMP}"
    staging.mkdir(parents=True)
    record = make_record(staging)
    assert record.write(staging) is True
    assert find_record(cfg, record.temp_name) == record
    assert find_record(cfg, "unknown-temp-name") is None
