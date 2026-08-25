"""Operator guards for the destructive overwrite restore path.

An overwrite restore force-stops the live VM and replaces its disks, so two
independent safety layers sit in front of it:

* an interactive confirmation (skippable with ``--yes``, refused outright
  when stdin is not a TTY so scripts must opt in explicitly), and
* a pre-restore safety backup of the current VM (skippable with
  ``--no-pre-backup``) so the state being destroyed is recoverable.
"""

from __future__ import annotations

import sys

from .backup import backup_vm
from .config import Config
from .logging_json import event
from .shell import CommandError, run
from .vms import VM

_CONFIRM_PROMPT = (
    "WARNING: this will OVERWRITE VM {vm!r} on this host.\n"
    "The VM will be force-stopped (downtime), its disks replaced with the\n"
    "backup contents, and its current definition swapped for the backed-up\n"
    "one. Type 'yes' to continue: "
)


def _confirm_overwrite(vm_name: str) -> bool:
    if not sys.stdin.isatty():
        event(
            "error",
            "refusing overwrite restore without confirmation",
            vm=vm_name,
            reason="stdin is not a TTY, so the interactive 'yes' confirmation cannot be asked",
            hint=f"re-run with -y/--yes to confirm overwriting VM {vm_name!r} non-interactively",
        )
        return False
    print(_CONFIRM_PROMPT.format(vm=vm_name), end="", file=sys.stderr, flush=True)
    try:
        answer = input()
    except EOFError:
        answer = ""
    if answer.strip().lower() in {"y", "yes"}:
        return True
    event("error", "overwrite restore cancelled by operator", vm=vm_name)
    return False


def _pre_restore_backup(config: Config, vm_name: str, vm_uuid: str) -> bool:
    try:
        state = run(["virsh", "-c", config.get("LIBVIRT_URI"), "domstate", "--", vm_name]).stdout.strip()
    except (CommandError, OSError) as exc:
        event(
            "error",
            "pre-restore backup could not read VM state",
            vm=vm_name,
            error=str(exc),
            hint="check that libvirt is reachable at LIBVIRT_URI, then re-run the restore",
        )
        return False
    vm = VM(name=vm_name, state=state, uuid=vm_uuid)
    if not vm.running:
        event(
            "error",
            "cannot take the pre-restore safety backup: the VM is not running",
            vm=vm_name,
            state=state,
            reason=(
                "only a running VM can be snapshotted, so the current disk state of this "
                f"{state or 'offline'} VM cannot be backed up before it is overwritten"
            ),
            hint=(
                "re-run with --no-pre-backup to overwrite anyway; be aware that this permanently "
                "discards everything the VM wrote after its most recent backup -- afterwards you "
                "can only restore points that already exist in list-restore-points, not the VM's "
                "current on-disk state"
            ),
        )
        return False
    event("info", "taking pre-restore safety backup before overwrite", vm=vm_name)
    try:
        backed_up = backup_vm(config, vm)
    except ValueError as exc:
        event("error", "pre-restore safety backup rejected VM identity", vm=vm_name, error=str(exc))
        return False
    if not backed_up:
        event(
            "error",
            "pre-restore safety backup failed; aborting restore",
            vm=vm_name,
            hint="fix the backup failure or pass --no-pre-backup to overwrite anyway",
        )
        return False
    event("info", "pre-restore safety backup completed", vm=vm_name)
    return True


def overwrite_allowed(config: Config, vm_name: str, vm_uuid: str, *, assume_yes: bool, pre_backup: bool) -> bool:
    """Run both overwrite guards; ``True`` means the overwrite may proceed."""
    if not assume_yes and not _confirm_overwrite(vm_name):
        return False
    if not pre_backup:
        event("warning", "pre-restore safety backup skipped (--no-pre-backup)", vm=vm_name)
        return True
    return _pre_restore_backup(config, vm_name, vm_uuid)
