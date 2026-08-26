"""fstab/mount consistency across nodes sharing one backup tree.

Every node that mounts the shared backup storage must mount the same
upstream export (same server address) the same way, or backups land in
different places and behave differently per node. The first node records
its ``/etc/fstab`` entry for the ``BACKUP_PATH`` mount into the backup tree
(``BACKUP_PATH/libvirt-backup-mounts.json``); preflight on every node then
verifies the local fstab entry matches the recorded one and that the live
mount (``/proc/self/mounts``) agrees with fstab. Mismatches fail preflight
before any backup is attempted, and the error prints both fstab entries so
the operator can copy the line from a working node.

Controlled by ``BACKUP_REQUIRE_FSTAB_CONSISTENCY`` (enabled by default).
If the NFS server address changes on purpose: update ``/etc/fstab`` on ALL
nodes, remount, then run ``push-config`` on one node to re-record the
shared entry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from .config import Config, prefixed
from .kopia_client import as_string_keyed
from .logging_json import event

MOUNT_METADATA_NAME = "libvirt-backup-mounts.json"
# Escapes used by both fstab(5) and /proc/self/mounts for embedded whitespace.
_OCTAL_ESCAPES = (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\"))


@dataclass(frozen=True)
class MountEntry:
    source: str
    mount_point: str
    fstype: str
    options: tuple[str, ...]

    def render(self) -> str:
        return f"{self.source} {self.mount_point} {self.fstype} {','.join(self.options)}"


def _unescape(field: str) -> str:
    for escape, char in _OCTAL_ESCAPES:
        field = field.replace(escape, char)
    return field


def parse_mount_table(text: str) -> list[MountEntry]:
    """Parse fstab / ``/proc/self/mounts`` text into entries (comments skipped)."""
    entries: list[MountEntry] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4:
            continue
        source, mount_point, fstype, options = (_unescape(field) for field in fields[:4])
        normalized_mount = mount_point.rstrip("/") or "/"
        entries.append(MountEntry(source, normalized_mount, fstype, tuple(options.split(","))))
    return entries


def fstab_path(config: Config) -> Path:
    return prefixed("/etc/fstab", config.prefix)


def proc_mounts_path(config: Config) -> Path:
    return prefixed("/proc/self/mounts", config.prefix)


def _read_table(path: Path) -> list[MountEntry]:
    try:
        return parse_mount_table(path.read_text(encoding="utf-8"))
    except OSError:
        return []


def _covering_mount_point(path: Path, entries: list[MountEntry]) -> str | None:
    """Most specific mount point that is ``path`` itself or an ancestor of it."""
    known = {entry.mount_point for entry in entries}
    for candidate in (str(path), *(str(parent) for parent in path.parents)):
        if candidate in known:
            return candidate
    return None


def _entry_for_mount_point(entries: list[MountEntry], mount_point: str) -> MountEntry | None:
    # Last match wins, matching mount(8) behavior when a line is repeated.
    match: MountEntry | None = None
    for entry in entries:
        if entry.mount_point == mount_point:
            match = entry
    return match


@dataclass(frozen=True)
class LocalMountState:
    mount_point: str | None
    live: MountEntry | None
    fstab: MountEntry | None


def local_mount_state(config: Config) -> LocalMountState:
    backup_path = config.path_value("BACKUP_PATH")
    live_entries = _read_table(proc_mounts_path(config))
    fstab_entries = _read_table(fstab_path(config))
    mount_point = _covering_mount_point(backup_path, live_entries)
    if mount_point is None:
        mount_point = _covering_mount_point(backup_path, fstab_entries)
    if mount_point is None:
        return LocalMountState(None, None, None)
    return LocalMountState(
        mount_point,
        _entry_for_mount_point(live_entries, mount_point),
        _entry_for_mount_point(fstab_entries, mount_point),
    )


def metadata_path(config: Config) -> Path | None:
    """``BACKUP_PATH/libvirt-backup-mounts.json``; BACKUP_PATH is used verbatim
    (not run through the install ``--prefix``), matching ``config_sync``."""
    backup_path = config.get("BACKUP_PATH").strip()
    if not backup_path:
        return None
    return Path(backup_path) / MOUNT_METADATA_NAME


def _load_document(path: Path) -> dict[str, object]:
    try:
        parsed: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "mounts": {}}
    document = as_string_keyed(parsed)
    if not isinstance(document.get("mounts"), dict):
        document["mounts"] = {}
    return document


def _entry_from_record(record: object) -> MountEntry | None:
    fields = as_string_keyed(record)
    source = fields.get("source")
    mount_point = fields.get("mount_point")
    fstype = fields.get("fstype")
    options = fields.get("options")
    if not (isinstance(source, str) and isinstance(mount_point, str) and isinstance(fstype, str)):
        return None
    if not isinstance(options, list):
        return None
    option_items = cast("list[object]", options)
    option_strings = [option for option in option_items if isinstance(option, str)]
    if len(option_strings) != len(option_items):
        return None
    return MountEntry(source, mount_point, fstype, tuple(option_strings))


def recorded_entry(config: Config) -> tuple[MountEntry | None, str]:
    """The shared recorded entry for this BACKUP_PATH plus who recorded it."""
    dest = metadata_path(config)
    if dest is None:
        return None, ""
    mounts = as_string_keyed(_load_document(dest).get("mounts"))
    record = as_string_keyed(mounts.get(config.get("BACKUP_PATH").strip()))
    entry = _entry_from_record(record)
    recorded_by = record.get("recorded_by")
    return entry, recorded_by if isinstance(recorded_by, str) else ""


def consistency_failures(config: Config) -> list[str]:
    """Preflight failures when this node's fstab/mount setup diverges.

    With a single node the check only fires once the local fstab drifts from
    the entry that node itself recorded on its first successful run.
    """
    if not config.get("BACKUP_PATH").strip() or not config.enabled("BACKUP_REQUIRE_FSTAB_CONSISTENCY"):
        return []
    state = local_mount_state(config)
    failures: list[str] = []
    if (
        state.fstab is not None
        and state.live is not None
        and ":" in state.fstab.source
        and state.live.source != state.fstab.source
    ):
        failures.append(
            f"BACKUP_PATH mount does not match /etc/fstab: mounted from '{state.live.source}' but fstab "
            f"says '{state.fstab.source}'; remount {state.mount_point} so the live mount matches fstab"
        )
    recorded, recorded_by = recorded_entry(config)
    if recorded is None:
        return failures
    recorded_label = f"'{recorded.render()}'" + (f" (recorded by {recorded_by})" if recorded_by else "")
    if state.fstab is None:
        failures.append(
            f"no /etc/fstab entry found for the BACKUP_PATH mount; the joined nodes use {recorded_label}; "
            "copy that fstab line from a working node, mount it, and re-run"
        )
        return failures
    if (
        state.fstab.source != recorded.source
        or state.fstab.fstype != recorded.fstype
        or set(state.fstab.options) != set(recorded.options)
    ):
        failures.append(
            "fstab entry for the BACKUP_PATH mount differs from the entry recorded in the backup tree: "  # noqa: S608 - prose failure message, not SQL
            f"this host has '{state.fstab.render()}' but the recorded entry is {recorded_label}. "
            "Every node must mount the same upstream server (same IP/export) with the same fstab setup; "
            "copy the fstab line from a working joined node. If the NFS server address changed on purpose, "
            "update /etc/fstab on ALL nodes, remount, and run push-config on one node to re-record it. "
            "Set BACKUP_REQUIRE_FSTAB_CONSISTENCY=false to disable this check."
        )
    return failures


def record_local_mount(config: Config, *, overwrite: bool = False) -> None:
    """Best-effort publish of this host's fstab entry to the backup tree.

    First writer wins unless ``overwrite`` (used by ``push-config`` after a
    deliberate change such as a new NFS server address). Failures are
    warnings: publishing metadata must not fail an otherwise-good run.
    """
    dest = metadata_path(config)
    if dest is None or not config.enabled("BACKUP_REQUIRE_FSTAB_CONSISTENCY"):
        return
    state = local_mount_state(config)
    if state.fstab is None or state.mount_point == "/":
        # Nothing meaningful to share: the backup dir sits on the root
        # filesystem or the mount is not declared in fstab.
        event("info", "no dedicated fstab entry for BACKUP_PATH; mount metadata not published")
        return
    key = config.get("BACKUP_PATH").strip()
    document = _load_document(dest)
    mounts = as_string_keyed(document.get("mounts"))
    if key in mounts and not overwrite:
        return
    mounts[key] = {
        "mount_point": state.fstab.mount_point,
        "source": state.fstab.source,
        "fstype": state.fstab.fstype,
        "options": list(state.fstab.options),
        "recorded_by": config.get("HOST_ID"),
        "recorded_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }
    document["mounts"] = mounts
    try:
        tmp = dest.with_name(f".{dest.name}.tmp")
        tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(dest)
    except OSError as exc:
        event("warning", "failed to publish mount metadata", path=str(dest), error=str(exc))
        return
    event("info", "published mount metadata", path=str(dest), entry=state.fstab.render())
