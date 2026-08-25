"""List, stop, and remove temp-restore clone VMs.

Management is driven by the on-disk records under the temp-restore root, not
by libvirt name matching alone: ``remove`` cross-checks that the domain that
currently owns the recorded name still has the recorded clone UUID before it
destroys or undefines anything, so it can never take down an unrelated VM.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import Config
from .logging_json import event
from .shell import CommandError, run
from .storage import resolved_path_is_within
from .temp_restore_state import TempRestoreRecord, find_record, load_records, temp_restore_root
from .vms import is_safe_vm_name

_HEADERS = ("temp-name", "state", "source-vm-name", "source-vm-uuid", "timestamp", "created-at")


def _domain_state(config: Config, vm_name: str) -> str:
    try:
        return run(["virsh", "-c", config.get("LIBVIRT_URI"), "domstate", "--", vm_name]).stdout.strip()
    except (CommandError, OSError):
        return "missing"


def _format_rows(rows: list[tuple[TempRestoreRecord, str]]) -> str:
    cells: list[tuple[str, ...]] = [_HEADERS]
    for record, state in rows:
        cells.append(
            (record.temp_name, state, record.source_vm_name, record.source_vm_uuid, record.timestamp, record.created_at)
        )
    widths = [max(len(cell[i]) for cell in cells) for i in range(len(_HEADERS))]
    lines = ["  ".join(cell[i].ljust(widths[i]) for i in range(len(_HEADERS))).rstrip() for cell in cells]
    return "\n".join(lines)


def _format_json(rows: list[tuple[TempRestoreRecord, str]]) -> str:
    payload = [
        {
            "created_at": record.created_at,
            "source_host_id": record.host_id,
            "source_vm_name": record.source_vm_name,
            "source_vm_uuid": record.source_vm_uuid,
            "staging": record.staging,
            "state": state,
            "temp_name": record.temp_name,
            "temp_uuid": record.temp_uuid,
            "timestamp": record.timestamp,
        }
        for record, state in rows
    ]
    return json.dumps(payload, sort_keys=True)


def list_temp_restores(config: Config, *, json_output: bool = False) -> int:
    rows = [(record, _domain_state(config, record.temp_name)) for record in load_records(config)]
    if json_output:
        print(_format_json(rows))
        return 0
    if not rows:
        event("info", "no temp restore VMs found")
        return 0
    print(_format_rows(rows))
    return 0


def _resolve_record(config: Config, temp_name: str) -> TempRestoreRecord | None:
    if not is_safe_vm_name(temp_name):
        event("error", "temp restore name is not a valid VM name", vm=temp_name)
        return None
    record = find_record(config, temp_name)
    if record is None:
        event("error", "no temp restore VM with that name", vm=temp_name, hint="see temp-restore list")
    return record


def _destroy_domain(config: Config, vm_name: str) -> bool:
    try:
        run(["virsh", "-c", config.get("LIBVIRT_URI"), "destroy", "--", vm_name])
    except CommandError as exc:
        event("info", "destroy returned nonzero (likely already off)", vm=vm_name, stderr=exc.result.stderr.strip())
    except OSError as exc:
        event("error", "virsh destroy unavailable", vm=vm_name, error=str(exc))
        return False
    return True


def stop_temp_restore(config: Config, temp_name: str) -> int:
    record = _resolve_record(config, temp_name)
    if record is None:
        return 1
    if not _destroy_domain(config, record.temp_name):
        return 1
    event("info", "temp restore VM stopped", vm=record.temp_name, state=_domain_state(config, record.temp_name))
    return 0


def _current_domain_uuid(config: Config, vm_name: str) -> tuple[bool, str | None]:
    """``(ok, uuid)``: ok=False means virsh itself was unusable."""
    try:
        result = run(["virsh", "-c", config.get("LIBVIRT_URI"), "domuuid", "--", vm_name])
    except CommandError:
        return True, None
    except OSError as exc:
        event("error", "virsh domuuid unavailable", vm=vm_name, error=str(exc))
        return False, None
    return True, result.stdout.strip().lower() or None


def _undefine_domain(config: Config, vm_name: str) -> bool:
    try:
        run(["virsh", "-c", config.get("LIBVIRT_URI"), "undefine", "--nvram", "--checkpoints-metadata", "--", vm_name])
    except CommandError as exc:
        event("error", "temp restore undefine failed", vm=vm_name, stderr=exc.result.stderr.strip())
        return False
    except OSError as exc:
        event("error", "virsh undefine unavailable", vm=vm_name, error=str(exc))
        return False
    return True


def _remove_staging(config: Config, record: TempRestoreRecord) -> bool:
    staging = Path(record.staging)
    if not resolved_path_is_within(temp_restore_root(config), staging):
        event("error", "temp restore staging path is outside the state dir", path=record.staging)
        return False
    try:
        shutil.rmtree(staging)
    except FileNotFoundError:
        pass
    except OSError as exc:
        event("error", "temp restore staging removal failed", path=record.staging, error=str(exc))
        return False
    return True


def remove_temp_restore(config: Config, temp_name: str) -> int:
    record = _resolve_record(config, temp_name)
    if record is None:
        return 1
    ok, current_uuid = _current_domain_uuid(config, record.temp_name)
    if not ok:
        return 1
    if current_uuid is not None and current_uuid != record.temp_uuid:
        event(
            "error",
            "domain name now belongs to a different VM; refusing to undefine",
            vm=record.temp_name,
            domain_uuid=current_uuid,
            recorded_uuid=record.temp_uuid,
        )
        return 1
    if current_uuid is not None and (
        not _destroy_domain(config, record.temp_name) or not _undefine_domain(config, record.temp_name)
    ):
        return 1
    if not _remove_staging(config, record):
        return 1
    event("info", "temp restore VM removed", vm=record.temp_name, staging=record.staging)
    return 0
