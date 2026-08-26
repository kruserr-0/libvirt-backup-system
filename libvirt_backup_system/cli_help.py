"""Long-form help text rendered by the CLI argument parser.

The strings live in their own module so cli.py stays focused on argument-
parser wiring and the help text can be read end-to-end without the parser
scaffolding in the way. Every string here is rendered verbatim by
``argparse.RawDescriptionHelpFormatter``, so leading indentation and blank
lines matter.
"""

from __future__ import annotations

# Re-exported: install/add-node/show-token/push-config/pull-config help lives
# in cli_help_install (LOC ceiling) but stays addressable as cli_help.*.
from .cli_help_install import (
    ADD_NODE_DESCRIPTION,
    ADD_NODE_HELP,
    INSTALL_DESCRIPTION,
    INSTALL_HELP,
    PULL_CONFIG_DESCRIPTION,
    PULL_CONFIG_HELP,
    PUSH_CONFIG_DESCRIPTION,
    PUSH_CONFIG_HELP,
    SHOW_TOKEN_HELP,
)

__all__ = [
    "ADD_NODE_DESCRIPTION",
    "ADD_NODE_HELP",
    "CHECK_DESCRIPTION",
    "CHECK_HELP",
    "INSTALL_DESCRIPTION",
    "INSTALL_HELP",
    "PULL_CONFIG_DESCRIPTION",
    "PULL_CONFIG_HELP",
    "PUSH_CONFIG_DESCRIPTION",
    "PUSH_CONFIG_HELP",
    "SHOW_TOKEN_HELP",
]

PROGRAM_DESCRIPTION = """\
libvirt-backup-system orchestrates kopia-backed backups of every running
libvirt VM on this host, writing snapshots into a per-host kopia repository
under ``BACKUP_PATH/<host-id>/kopia-repo/``. Backups are normally taken by
the installed systemd timer (default OnCalendar: *-*-* 02:30:00 UTC) so
manual ``run`` invocations are only needed for ad-hoc or recovery work.

Only running VMs are backed up. Offline VMs are logged as ``skipping vm
because it is offline`` and skipped; bring the VM up to back it up.

Configuration lives in /etc/libvirt-backup-system/libvirt-backup.env.
Config changes flow through the shared NFS tree with an explicit push/pull
pair: edit + ``start`` + ``push-config`` on one node, then ``pull-config`` +
``start`` on every other node (see the workflow below)."""


PROGRAM_EPILOG = """\
Common workflows:

  First install and activate the schedules:
    sudo env BACKUP_PATH=/mnt/qnap-backups libvirt-backup-system install
    sudo libvirt-backup-system show-token
    sudoedit /etc/libvirt-backup-system/libvirt-backup.env
    sudo libvirt-backup-system start
    sudo libvirt-backup-system check
    sudo libvirt-backup-system add-node

  Change configuration (flows through the shared NFS tree via push/pull):
    # on the node you edited:
    sudoedit /etc/libvirt-backup-system/libvirt-backup.env
    sudo libvirt-backup-system start        # 1. apply locally
    sudo libvirt-backup-system push-config  # 2. publish for the cluster

    # on every other node:
    sudo libvirt-backup-system pull-config  # 3. take over the shared config
    sudo libvirt-backup-system start        # 4. apply locally

  Daily operation:
    sudo libvirt-backup-system status
    sudo libvirt-backup-system run        # backs up in the background
    sudo libvirt-backup-system log -f     # follow the running backup
    sudo libvirt-backup-system list-vms
    sudo libvirt-backup-system doctor

  Restore a single backup run (OVERWRITES the live VM -- downtime):
    sudo libvirt-backup-system list-restore-points | grep my-vm
    sudo libvirt-backup-system restore <VM_UUID> <TIMESTAMP>

  Restore into a throwaway clone VM instead (no downtime, original untouched):
    sudo libvirt-backup-system temp-restore restore <VM_UUID> <TIMESTAMP>
    sudo libvirt-backup-system temp-restore remove <VM_NAME>-temp-<TIMESTAMP>

Run ``libvirt-backup-system <subcommand> --help`` for the full reference on any
subcommand. At the top level, no argument, ``help``, and ``?`` are aliases for
``--help``. The ``restore`` help in particular documents the overwrite-vs-
turnkey decision, the staging directory layout, and the safety guarantees."""


UNINSTALL_HELP = "Remove installed files. Config/state/logs/backups are kept unless --purge-* is passed."
UNINSTALL_DESCRIPTION = """\
Disable timers, stop services, and remove the installed wrapper, opt package
copy, systemd unit files, and bash/fish completion scripts. If the opt path is itself
a Git checkout, the checkout is preserved. Config, state, logs, and on-disk
backups are preserved by default so an accidental uninstall does not destroy
data; use the --purge-* flags to remove them explicitly.

The on-disk kopia repo under BACKUP_PATH is never touched by uninstall; that
has to be removed by hand once the operator is sure the backups are no longer
needed."""


CHECK_HELP = "Run preflight: validate config, binaries, paths, and free space."
CHECK_DESCRIPTION = """\
Validate the environment before a backup run: config keys are present and
typed correctly, required binaries (virsh, qemu-nbd, nbdcopy, qemu-img, df,
kopia) are on PATH *and actually runnable* (a binary with a missing shared
library fails check instead of passing), libvirt is reachable, BACKUP_PATH
is writable and (with BACKUP_REQUIRE_NFS_MOUNT=true, the default) is a
mounted filesystem rather than a local directory, the /etc/fstab entry for
the backup mount matches the entry recorded by the first node (see
BACKUP_REQUIRE_FSTAB_CONSISTENCY), the scratch directory is writable,
KOPIA_PASSWORD_FILE exists with mode 600 and (under root) is owned by root,
and df reports enough free space to satisfy the estimated repo growth for
selected running VMs. When binaries are missing, the failure includes the
apt install command for the detected Debian/Ubuntu release.

``preflight`` is an alias of ``check``."""


DOCTOR_HELP = "Run the full preflight surface plus install/registration and last-run health."
DOCTOR_DESCRIPTION = """\
Superset of ``check``: runs the full preflight layer and then validates that
the wrapper, opt directory, and config file are in place; the systemd unit
files match what a fresh install would render (catches drift after editing
the env file without re-running install); all schedule timers are enabled and
active;
the local kopia repo is connected and accessible; local kopia maintenance
and lightweight verify probes pass; peer repos are reachable read-only with the shared
password; and the most recent libvirt-backup-system.service run completed
cleanly.

Use ``check`` for the pre-run preflight only; use ``doctor`` when you also
want install/registration/last-run health."""


RUN_HELP = "Start a background backup of every running VM via systemd. Alias: backup."
RUN_DESCRIPTION = """\
Manual backup invocation. Acquires the run lock, runs ``check``, and then
backs up every selected running VM. Offline VMs are logged as
``skipping vm because it is offline`` and skipped. Each running VM is
streamed disk-by-disk into the local kopia repo via
``kopia snapshot create --stdin-file`` and tagged with
``kind:disk,run-id:<uuid>,disk:<target>,vm-uuid:<uuid>``. A per-run
``kind:meta`` snapshot carries the manifest with the domain XML and disk
listing so restore can reconstruct the VM without re-asking libvirt.

On a systemd host the backup runs in the background: ``run``/``backup``
dispatches the work to the ``libvirt-backup-system.service`` unit with
``systemctl start --no-block`` and returns as soon as systemd accepts the job.
The backup then runs under systemd (PID 1), so it survives logging out,
closing the terminal, or dropping the SSH session. Follow a running backup
with ``libvirt-backup-system log -f`` (live stream, like ``docker logs -f``)
and review earlier runs with ``libvirt-backup-system log``.

Manual runs require the systemd schedule to have been activated first with
``start`` -- on a systemd host, ``run``/``backup`` exits non-zero with
``backup service is not running`` instead of starting an ad-hoc backup if
the unit has not been installed and activated. When systemd is unavailable,
or ``--config``/``--prefix`` is set, or you are already executing inside the
unit, the backup instead runs in-process in the foreground.

Retention is driven by the kopia global policy (KEEP_LATEST / KEEP_HOURLY /
KEEP_DAILY / KEEP_WEEKLY / KEEP_MONTHLY / KEEP_ANNUAL) applied at install
time and refreshed on ``start``; old snapshots are reaped by the periodic
``kopia maintenance`` units rather than at the tail of ``run``."""


LOG_HELP = "Show backup logs from the journal; -f streams live like docker logs -f. Alias: logs."
LOG_DESCRIPTION = """\
Show the systemd journal for the backup units, modeled on ``docker logs``.

By default ``log`` prints the most recent 50 lines from the
``libvirt-backup-system.service`` unit (the backup orchestrator) and exits.
Pass ``-f``/``--follow`` to keep the stream open and print new lines as they
are written -- the same live output a foreground run would show. Following is
read-only: Ctrl-C stops following, it does not stop the backup, which keeps
running under systemd.

  sudo libvirt-backup-system run         # start a backup in the background
  sudo libvirt-backup-system log -f      # follow it live

Options:
  -n, --lines N   How many recent lines to print before following. Accepts a
                  non-negative integer or ``all``. Default: 50.
  -f, --follow    Stream new lines as they arrive instead of exiting.

A trailing component selects which unit's journal to read (default ``run``):
  run               the backup orchestrator (libvirt-backup-system.service)
  check             the preflight unit
  maintenance       kopia quick maintenance
  maintenance-full  kopia full maintenance / GC
  verify            kopia snapshot verify
  all               the backup, maintenance, full-maintenance, and verify
                    units interleaved

  sudo libvirt-backup-system log verify
  sudo libvirt-backup-system log -f all
  sudo libvirt-backup-system log -n all run"""


START_HELP = "Install/refresh systemd units from the env file and activate timers."
START_DESCRIPTION = """\
Render the systemd unit files from the current env file, reload systemd, and
enable + start libvirt-backup-system.timer,
libvirt-backup-system-maintenance.timer,
libvirt-backup-system-maintenance-full.timer, and
libvirt-backup-system-verify.timer. This activates the schedules only -- it
does not run a backup immediately. Use ``start`` after ``install`` and after
every edit to /etc/libvirt-backup-system/libvirt-backup.env that changes
BACKUP_PATH or timer settings. ``start`` is steps 1 and 4 of the config
workflow: on the node you edited it applies the change locally (then
``push-config`` publishes it for the cluster); on every other node it
applies the config just taken over with ``pull-config``. Use ``run`` for a
manual backup."""


STATUS_HELP = "Print systemctl status for the installed timers and services."


LIST_VMS_HELP = "List selected VMs after VM_BLACKLIST is applied."
LIST_VMS_DESCRIPTION = """\
Print one row per VM that ``run`` would currently consider, after
VM_BLACKLIST (UUID-based) is applied. Default output is one
``<name>\\t<state>\\t<uuid>`` line per VM; ``--json`` emits a JSON array
suitable for piping into ``jq``. Pass ``--include-blacklisted`` to also list
VMs that are present in libvirt but currently filtered out by VM_BLACKLIST."""


VERIFY_HELP = "Run ``kopia snapshot verify`` against discovered kopia repos."
VERIFY_DESCRIPTION = """\
Replay every snapshot in this host's local kopia repo through
``kopia snapshot verify --max-errors=0 --verify-files-percent=...`` to
confirm the repo is internally consistent. Pass
``--include-hosts=HOST_ID[,HOST_ID...]`` to additionally verify the named
peer repos discovered under ``BACKUP_PATH/<host>/kopia-repo/``; without the
flag only the local repo is checked. VM_BLACKLIST is intentionally ignored:
a VM that was added to the blacklist may still have valid older snapshots
that the operator wants to verify."""


LIST_RESTORE_POINTS_HELP = "List every restorable backup run across all hosts and VMs."
LIST_RESTORE_POINTS_DESCRIPTION = """\
Connect read-only to every per-host kopia repo discovered under
``BACKUP_PATH/<host>/kopia-repo/`` (including the local repo) and list every
``kind:meta`` snapshot -- one per backup run. Copy the VM_UUID and per-run
TIMESTAMP columns straight into ``restore``. Rows include source host, VM
name, and RUN_ID, and are grouped by source host so backups taken on a
different KVM host are visible alongside the local ones."""


DU_HELP = "Show backup disk usage by host or VM."


KOPIA_PASSTHROUGH_HELP = "Run a raw ``kopia`` command against a managed repo (advanced)."
KOPIA_PASSTHROUGH_DESCRIPTION = """\
Hidden escape hatch for ad-hoc ``kopia`` invocations against a repo this
tool already manages. The wrapper resolves the correct
``--config-file=...`` and KOPIA_PASSWORD_FILE for you and then execs the
operator's ``kopia`` arguments verbatim.

By default the local host's repo connection-config is used:
  sudo libvirt-backup-system kopia-passthrough -- snapshot list

To target a peer repo discovered under ``BACKUP_PATH/<host-id>/kopia-repo/``
pass ``--host-id=<id>``:
  sudo libvirt-backup-system kopia-passthrough --host-id=other-kvm -- \\
       snapshot list --tags=kind:meta

Use a literal ``--`` between this command's flags and the kopia argv tail to
keep kopia's own ``--flags`` from being captured by argparse. The kopia
process inherits this command's stdin/stdout/stderr; its exit code is the
wrapper's exit code."""
