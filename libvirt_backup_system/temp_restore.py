"""Restore a backup run into a throwaway clone VM beside the original.

The clone gets a fresh libvirt UUID and the name ``<vm-name>-temp-<timestamp>``,
its disks land under the temp-restore state directory, and its XML is scrubbed
of everything that must be unique per running domain (MACs, graphics ports,
hostdevs, nvram). The original VM, its disks, and the backups are only read,
never written.
"""

from __future__ import annotations

import datetime as dt
import shutil
import uuid as uuid_module
from pathlib import Path

from .atomic_io import stamp_is_safe
from .config import Config
from .list_restore_points import BackupRow
from .logging_json import event
from .manifest import Manifest
from .paths import runtime_backup_path_ok
from .restore import RESTORE_TIMESTAMP_RE, match_row
from .restore_define import define_restored_domain
from .restore_io import disk_snapshot_id, manifest_matches_request, restore_manifest, stream_disk_to_qcow2
from .restore_state import restore_vm_power
from .restore_xml import turnkey_dest_map
from .shell import CommandError, run
from .storage import subpath_is_safe
from .temp_restore_state import TempRestoreRecord, temp_restore_root
from .temp_restore_xml import rewrite_for_temp
from .vms import is_safe_vm_name, is_safe_vm_uuid

TEMP_CONFIG_FILE = "libvirt-backup-system-temp-restored.xml"
TEMP_NAME_SEPARATOR = "-temp-"


def temp_vm_name(vm_name: str, timestamp: str) -> str:
    return f"{vm_name}{TEMP_NAME_SEPARATOR}{timestamp}"


def _prepare_staging(config: Config, vm_uuid: str, timestamp: str) -> Path | None:
    root = temp_restore_root(config)
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        event("error", "temp restore root creation failed", path=str(root), error=str(exc))
        return None
    staging = root / f"{vm_uuid}-{timestamp}"
    if not subpath_is_safe(root, staging):
        event("error", "temp restore staging path is unsafe", path=str(staging))
        return None
    if staging.exists():
        # Unlike the overwrite restore staging, an existing directory here can
        # belong to a *running* clone, so it is never auto-deleted.
        event(
            "error",
            "a temp restore for this restore point already exists",
            path=str(staging),
            hint="finish with it and run temp-restore remove first",
        )
        return None
    try:
        staging.mkdir(mode=0o700)
    except OSError as exc:
        event("error", "temp restore staging dir creation failed", path=str(staging), error=str(exc))
        return None
    return staging


def _domain_exists(config: Config, vm_name: str) -> bool | None:
    """True/False when virsh answered; None when the check itself failed."""
    try:
        run(["virsh", "-c", config.get("LIBVIRT_URI"), "domuuid", "--", vm_name])
    except CommandError:
        return False
    except OSError as exc:
        event("error", "virsh domuuid unavailable", vm=vm_name, error=str(exc))
        return None
    return True


def _materialize_disks(
    config: Config, row: BackupRow, manifest: Manifest, dest_map: dict[str, Path], *, verbose: bool
) -> bool:
    for disk in manifest.disks:
        dest = dest_map[disk.target]
        snap_id = disk_snapshot_id(config, row, disk.target)
        if snap_id is None:
            return False
        if not stream_disk_to_qcow2(config, row, snap_id, disk.snapshot_filename, dest):
            return False
        if verbose:
            event("info", "restored temp disk", target=disk.target, path=str(dest))
    return True


def _fail_cleanup(staging: Path) -> int:
    # Only safe before the domain is defined: afterwards the disks are the
    # clone's live storage and cleanup goes through ``temp-restore remove``.
    shutil.rmtree(staging, ignore_errors=True)
    return 1


def _write_record(row: BackupRow, manifest: Manifest, staging: Path, temp_name: str) -> str | None:
    temp_uuid = str(uuid_module.uuid4())
    record = TempRestoreRecord(
        temp_name=temp_name,
        temp_uuid=temp_uuid,
        source_vm_name=manifest.vm_name,
        source_vm_uuid=manifest.vm_uuid,
        timestamp=manifest.timestamp,
        host_id=row.host_id,
        run_id=row.run_id,
        created_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        staging=str(staging),
        disks=tuple(str(path) for path in turnkey_dest_map(manifest, staging).values()),
    )
    return temp_uuid if record.write(staging) else None


def _define_and_start(config: Config, staging: Path, temp_name: str, temp_uuid: str, domain_xml: str) -> int:
    xml_path = staging / TEMP_CONFIG_FILE
    xml_path.write_text(domain_xml, encoding="utf-8")
    if not define_restored_domain(config, xml_path, temp_uuid, temp_name):
        return _fail_cleanup(staging)
    # Point of no return: the clone is defined, so its files stay on disk and
    # cleanup is temp-restore remove. Always start it — the whole point of a
    # temp restore is a running VM to copy files out of.
    if not restore_vm_power(config, temp_name, "running", runner=run):
        event(
            "error",
            "temp restore VM defined but failed to start",
            vm=temp_name,
            hint="inspect with virsh, then clean up with temp-restore remove",
        )
        return 1
    event("info", "temp restore completed", vm=temp_name, vm_uuid=temp_uuid, output=str(staging))
    return 0


def restore_temp(
    config: Config,
    vm_uuid: str,
    timestamp: str,
    *,
    host_id: str | None = None,
    run_id: str | None = None,
    verbose: bool = True,
) -> int:
    if not is_safe_vm_uuid(vm_uuid):
        event("error", "temp restore vm_uuid is not a valid UUID", vm_uuid=vm_uuid)
        return 1
    if not stamp_is_safe(timestamp) or RESTORE_TIMESTAMP_RE.fullmatch(timestamp) is None:
        event("error", "temp restore timestamp is malformed", timestamp=timestamp)
        return 1
    if not runtime_backup_path_ok(config):
        return 1
    row = match_row(config, vm_uuid, timestamp, host_id, run_id)
    if row is None:
        event("error", "temp restore found no backup matching uuid and timestamp", vm_uuid=vm_uuid, timestamp=timestamp)
        return 1
    if (staging := _prepare_staging(config, vm_uuid, timestamp)) is None:
        return 1
    if (manifest := restore_manifest(config, row, staging)) is None:
        return _fail_cleanup(staging)
    if not manifest_matches_request(manifest, row, vm_uuid, timestamp):
        return _fail_cleanup(staging)
    if not is_safe_vm_name(manifest.vm_name):
        event("error", "manifest carries unsafe vm name", vm_name=manifest.vm_name)
        return _fail_cleanup(staging)
    temp_name = temp_vm_name(manifest.vm_name, timestamp)
    exists = _domain_exists(config, temp_name)
    if exists is None:
        return _fail_cleanup(staging)
    if exists:
        event(
            "error",
            "a domain with the temp restore name already exists",
            vm=temp_name,
            hint="remove it with temp-restore remove (or rename the unrelated domain), then re-run",
        )
        return _fail_cleanup(staging)
    dest_map = turnkey_dest_map(manifest, staging)
    if not _materialize_disks(config, row, manifest, dest_map, verbose=verbose):
        return _fail_cleanup(staging)
    rewritten = rewrite_for_temp(manifest.domain_xml, dest_map, staging)
    if rewritten is None:
        return _fail_cleanup(staging)
    temp_uuid = _write_record(row, manifest, staging, temp_name)
    if temp_uuid is None:
        return _fail_cleanup(staging)
    return _define_and_start(config, staging, temp_name, temp_uuid, rewritten)
