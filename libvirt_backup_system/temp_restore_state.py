"""On-disk records for temp-restore clone VMs.

Every ``temp-restore restore`` writes one ``temp-restore.json`` into the
clone's staging directory under
``/var/lib/libvirt-backup-system/temp-restore/<uuid>-<timestamp>/``. The
record ties the clone's libvirt identity (name + fresh UUID) back to the
restore point it came from so ``list``/``stop``/``remove`` can manage the
clone later — including refusing to undefine a domain whose UUID no longer
matches the recorded one.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import cast

from .atomic_io import atomic_write
from .config import Config, prefixed
from .logging_json import event

TEMP_RESTORE_DIR = Path("/var/lib/libvirt-backup-system/temp-restore")
RECORD_FILENAME = "temp-restore.json"


@dataclass(frozen=True)
class TempRestoreRecord:
    temp_name: str
    temp_uuid: str
    source_vm_name: str
    source_vm_uuid: str
    timestamp: str
    host_id: str
    run_id: str
    created_at: str
    staging: str
    disks: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, indent=2) + "\n"

    def write(self, directory: Path) -> bool:
        return atomic_write(
            directory / RECORD_FILENAME,
            self.to_json(),
            self.temp_name,
            "temp restore record write failed",
        )


def parse_record(text: str) -> TempRestoreRecord:
    """Strict parser: a corrupted or tampered record fails at read time."""
    parsed: object = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("temp restore record is not a JSON object")
    data = cast("dict[str, object]", parsed)
    raw_disks = data.get("disks")
    if not isinstance(raw_disks, list):
        raise ValueError("temp restore record disks field is not a list")
    disks: list[str] = []
    for disk in cast("list[object]", raw_disks):
        if not isinstance(disk, str):
            raise ValueError("temp restore record disk entry is not a string")
        disks.append(disk)
    return TempRestoreRecord(
        temp_name=_require_str(data, "temp_name"),
        temp_uuid=_require_str(data, "temp_uuid"),
        source_vm_name=_require_str(data, "source_vm_name"),
        source_vm_uuid=_require_str(data, "source_vm_uuid"),
        timestamp=_require_str(data, "timestamp"),
        host_id=_require_str(data, "host_id"),
        run_id=_require_str(data, "run_id"),
        created_at=_require_str(data, "created_at"),
        staging=_require_str(data, "staging"),
        disks=tuple(disks),
    )


def _require_str(record: dict[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise ValueError(f"temp restore record field {key!r} must be a string")
    return value


def read_record(path: Path) -> TempRestoreRecord:
    return parse_record(path.read_text(encoding="utf-8"))


def temp_restore_root(config: Config) -> Path:
    return prefixed(TEMP_RESTORE_DIR, config.prefix)


def load_records(config: Config) -> list[TempRestoreRecord]:
    """Read every parseable record under the temp-restore root.

    Unreadable or corrupt records are logged and skipped instead of failing
    the listing: one damaged clone directory must not hide the healthy ones
    from ``list``/``stop``/``remove``.
    """
    root = temp_restore_root(config)
    if not root.is_dir():
        return []
    records: list[TempRestoreRecord] = []
    for entry in sorted(root.iterdir()):
        record_path = entry / RECORD_FILENAME
        if not record_path.is_file():
            continue
        try:
            records.append(read_record(record_path))
        except (OSError, ValueError) as exc:
            event("warning", "skipping unreadable temp restore record", path=str(record_path), error=str(exc))
    return records


def find_record(config: Config, temp_name: str) -> TempRestoreRecord | None:
    for record in load_records(config):
        if record.temp_name == temp_name:
            return record
    return None
