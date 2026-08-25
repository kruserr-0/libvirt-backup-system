"""Long-form help text for the ``restore`` and ``temp-restore`` subcommands.

Split out of ``cli_help.py`` to keep each help module readable end-to-end.
Every string is rendered verbatim by ``argparse.RawDescriptionHelpFormatter``,
so leading indentation and blank lines matter.
"""

from __future__ import annotations

RESTORE_HELP = "Restore a backup run by OVERWRITING the live VM (destructive; downtime)."
RESTORE_DESCRIPTION = """\
Restore a single backup run identified by its (VM_UUID, TIMESTAMP) pair.

*** THIS COMMAND OVERWRITES THE EXISTING VM WHEN ONE MATCHES ***
  When the selected backup was taken on this host and a local libvirt domain
  with VM_UUID still exists, the live VM is FORCE-STOPPED (``virsh destroy``
  -- immediate downtime, no guest shutdown), its current definition is
  replaced with the backed-up one, and its disk files are OVERWRITTEN with
  the backup contents. Everything the VM wrote after the chosen TIMESTAMP is
  destroyed. If you only need to fish a few files out of a backup while the
  VM keeps running, use ``temp-restore`` instead -- it boots a throwaway
  clone next to the untouched original with zero downtime.

Safety rails in front of the overwrite:
  1. Interactive confirmation. The command prints what it is about to
     destroy and waits for a literal ``yes`` on stdin. Pass ``-y``/``--yes``
     to skip the prompt. When stdin is not a TTY (scripts, CI) the restore
     refuses to overwrite unless ``--yes`` is given.
  2. Pre-restore safety backup. Before touching anything the current VM is
     backed up with the normal backup pipeline, so the state you are about
     to destroy stays restorable. The restore aborts if that backup fails
     or if the VM is not running (an offline VM cannot be snapshotted).
     Pass ``--no-pre-backup`` to skip the safety backup -- for example when
     the VM is already broken or offline and you accept losing its current
     disk state. Skipping is permanent: the VM's current state is in no
     backup, so after the overwrite you can only restore points that already
     exist, not anything the VM wrote after its most recent backup.
  Neither guard applies to the non-destructive turnkey path below.

How to pick the arguments:
  Copy the ``vm-uuid`` and ``timestamp`` columns of any line printed by
  ``list-restore-points`` straight into this command. There is no rounding,
  no closest-match: TIMESTAMP is the exact per-run target.

How the snapshot is located:
  ``restore`` walks every per-host kopia repo discovered under
  ``BACKUP_PATH/<host>/kopia-repo/`` (not just the current HOST_ID) so a
  recovery host that mounted the backup tree can restore VMs that were
  taken on a different KVM host. The per-run ``kind:meta`` snapshot is
  matched by its ``vm-uuid`` and ``timestamp`` tags. If duplicate rows share
  that pair, pass ``--host-id`` or ``--run-id`` from ``list-restore-points``
  to select the intended run.

What action is chosen:
  OVERWRITE  Same host AND a local libvirt domain with VM_UUID exists.
             Destructive, causes downtime; guarded as described above.
             The current VM is force-shut-down (``virsh destroy``),
             undefined with ``--checkpoints-metadata`` (to clear any
             leftover libvirt checkpoints), and then redefined from the
             backup XML pointing at restored disks. Existing disk files
             are replaced. Refuses to proceed if the shutdown fails.

  TURNKEY    Anything else: cross-host recovery, or the local VM no longer
             exists. Non-destructive. Restored disks are written under
             /var/lib/libvirt-backup-system/restore/<uuid>-<timestamp>/ and
             the domain XML is rewritten so ``<source>`` elements point at
             the restored qcow2 files. The recovered VM is one
             ``virsh start`` away from booting.

What the underlying command runs:
  ``restore`` materializes the per-run manifest by streaming the meta
  snapshot via ``kopia snapshot restore``, then for each disk in the
  manifest pipes ``kopia snapshot restore <snap-id>/<file> -`` into
  ``qemu-img convert -f raw -O qcow2 -S 4096 -`` to produce a sparse qcow2
  at the chosen destination. By default restore prints only summary
  success/error events; pass ``-v``/``--verbose`` to log each restored
  disk path.

Safety guarantees:
  * VM_BLACKLIST is ignored: blacklisting scopes to *taking* new backups, not
    to restoring from existing ones.
  * The staging directory is recreated on every restore so a leftover from
    an interrupted earlier restore cannot contaminate the current one.
  * Holds the same run-lock as ``run`` to avoid reading a repo state that
    a concurrent backup is still writing into.

Example:
  sudo libvirt-backup-system list-restore-points | grep my-vm
  sudo libvirt-backup-system restore \\
       aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa 20260507T101112"""


TEMP_RESTORE_HELP = "Restore into a throwaway clone VM that runs beside the original (no downtime)."
TEMP_RESTORE_DESCRIPTION = """\
Restore a backup run into a temporary parallel VM without touching the
existing VM, its disks, or the backups.

The use case: production VM ``my-vm`` is running and must stay up, but you
need a file back from last night's backup. ``temp-restore restore`` boots a
clone of the backup next to the live VM; you log into the clone, ``scp`` the
file into the production VM, then throw the clone away with
``temp-restore remove``. Zero downtime, nothing overwritten.

So the clone can run at the same time as the original, its definition is
rewritten before it is defined:
  * fresh libvirt UUID and the name ``<vm-name>-temp-<TIMESTAMP>``
  * disks restored under
    /var/lib/libvirt-backup-system/temp-restore/<uuid>-<timestamp>/
    (the original disk files are never opened, let alone written)
  * NIC MAC addresses are dropped so libvirt generates new ones (the clone
    comes up on the same networks with different MACs -- give it a moment
    to DHCP a fresh lease, or set its IP from the console)
  * graphics devices switch to autoport so VNC/SPICE ports cannot collide
  * host-passthrough <hostdev> devices are removed (the original VM still
    owns the hardware)
  * UEFI nvram is relocated into the clone's own directory

The clone is always started after it is defined, regardless of the state
the source VM was in when the backup ran.

Subcommands:
  restore VM_UUID TIMESTAMP   restore a point into a new clone; same
                              argument UX as the top-level ``restore``
                              (copy both columns from list-restore-points,
                              disambiguate with --host-id/--run-id)
  list [--json]               show clone VMs and their current state
  stop TEMP_VM_NAME           force-stop a clone (virsh destroy; the clone
                              is disposable so no guest shutdown is tried)
  remove TEMP_VM_NAME         stop + undefine the clone and delete its
                              disks and state directory

Safety guarantees:
  * The original VM is never stopped, undefined, or written to.
  * Backups are only read, never modified.
  * One clone per (VM_UUID, TIMESTAMP): restoring the same point again is
    refused until the earlier clone is removed.
  * ``remove`` refuses to undefine a domain whose UUID no longer matches
    the recorded clone UUID, so it can never take down an unrelated VM
    that happens to reuse the name.
  * ``restore`` holds the same run-lock as ``run``/``restore``.

Example:
  sudo libvirt-backup-system list-restore-points | grep my-vm
  sudo libvirt-backup-system temp-restore restore \\
       aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa 20260507T101112
  # ... scp what you need out of my-vm-temp-20260507T101112 ...
  sudo libvirt-backup-system temp-restore remove my-vm-temp-20260507T101112"""


TEMP_RESTORE_RESTORE_HELP = "Restore a backup run into a new clone VM and start it."
TEMP_RESTORE_RESTORE_DESCRIPTION = """\
Restore the backup run identified by (VM_UUID, TIMESTAMP) into a new clone
VM named ``<vm-name>-temp-<TIMESTAMP>`` and start it. The existing VM keeps
running untouched; see ``temp-restore --help`` for the full contract.

Copy VM_UUID and TIMESTAMP verbatim from ``list-restore-points`` output; if
duplicate rows share the pair, disambiguate with --host-id/--run-id exactly
like the top-level ``restore`` command."""

TEMP_RESTORE_LIST_HELP = "List temp-restore clone VMs and their current state."
TEMP_RESTORE_STOP_HELP = "Force-stop a clone VM (virsh destroy); its disks and definition are kept."
TEMP_RESTORE_REMOVE_HELP = "Stop and undefine a clone VM and delete its restored disks."
TEMP_RESTORE_REMOVE_DESCRIPTION = """\
Stop the clone if it is running, undefine it, and delete its staging
directory (restored disks, domain XML, nvram, state record). Refuses to act
when the domain name now belongs to a VM whose UUID does not match the
recorded clone UUID. The original VM and the backups are never touched."""
