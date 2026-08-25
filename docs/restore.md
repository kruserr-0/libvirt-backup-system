# Restore commands

Reference for `list-restore-points`, `restore`, and `temp-restore`. The
`--help` text of each subcommand is the authoritative documentation; this
page is the overview.

## `list-restore-points`

Walks every per-host repo under `BACKUP_PATH/*/kopia-repo/`, connects
read-only with the shared token, lists `kind:meta` snapshots, and prints
one row per (host, VM UUID, timestamp). Copy the `vm-uuid` and `timestamp`
columns straight into `restore` or `temp-restore restore`.

```sh
sudo libvirt-backup-system list-restore-points
sudo libvirt-backup-system list-restore-points | grep my-vm
sudo libvirt-backup-system list-restore-points | less -S
```

Output columns:

```
source-host-id  vm-uuid  timestamp  run-id  consistency  vm-name
```

`source-host-id` is where the backup was taken. `run-id` joins the meta
snapshot to its disk snapshots for diagnostics and manual operations (see
[Kopia operations](kopia.md)). `consistency` is `filesystem`, `crash`, or
`unknown`; see [Backup consistency](backup-consistency.md).

## `restore`

Restores a single backup run identified by the `(vm_uuid, timestamp)` pair
from `list-restore-points`. The action is automatic:

- If the backup was taken on this host **and** a libvirt domain with that
  UUID exists locally, the VM is **overwritten in place**: force-stopped
  (downtime), undefined, and redefined from the backup with its disk files
  replaced. Everything the VM wrote after the chosen timestamp is destroyed.
- Otherwise the VM is staged and redefined from the backup XML on this host
  (turnkey one-click recovery on a different host or after the local VM has
  been removed). This path is non-destructive.

```sh
sudo libvirt-backup-system restore <vm-uuid> <timestamp>
sudo libvirt-backup-system restore aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa 20260507T101112
```

Two guards sit in front of the overwrite path (and only that path):

- **Interactive confirmation.** The command prints what it is about to
  destroy and waits for a literal `yes`. Pass `-y`/`--yes` to skip; when
  stdin is not a TTY (scripts, CI) the overwrite is refused unless `--yes`
  is given.
- **Pre-restore safety backup.** The current VM is backed up before its
  disks are overwritten, so the state being destroyed stays restorable.
  Pass `--no-pre-backup` to skip; that is also required to overwrite a VM
  that is not running, since an offline VM cannot be snapshotted — and it
  permanently discards everything the VM wrote after its most recent backup.

Pass `--host-id <source-host-id>` or `--run-id <run-id>` only when duplicate
rows share the same `(vm-uuid, timestamp)`. The timestamp is the exact
per-run target (no rounding, no closest-match). For same-host restores where
a local domain with the same UUID exists, disks are restored to temporary
sibling files first, then the VM is shut down, undefined, and the temporary
files replace the original per-disk source paths. Cross-host or fresh
restores write qcow2s under
`/var/lib/libvirt-backup-system/restore/<uuid>-<timestamp>/` and rewrite the
restored domain XML to those staged paths.

Internally each disk snapshot is piped through `qemu-img convert -f raw -O
qcow2 -S 4096` to produce a sparse qcow2 on the destination. The meta
snapshot is materialized to a tmp dir so the manifest's domain XML can be
read into `virsh define`. By default, restore prints only summary
success/error events; pass `-v`/`--verbose` to stream per-disk progress.

## `temp-restore`

Restores a backup run into a throwaway clone VM that runs **beside** the
untouched original — zero downtime, nothing overwritten. Use it to pull
files out of a backup while the production VM keeps running, then throw the
clone away.

```sh
sudo libvirt-backup-system temp-restore restore <vm-uuid> <timestamp>
# ... scp what you need out of <vm-name>-temp-<timestamp> ...
sudo libvirt-backup-system temp-restore list
sudo libvirt-backup-system temp-restore stop <vm-name>-temp-<timestamp>
sudo libvirt-backup-system temp-restore remove <vm-name>-temp-<timestamp>
```

So the clone can run at the same time as the original, its definition is
rewritten before it is defined: fresh libvirt UUID and the name
`<vm-name>-temp-<timestamp>`, disks restored under
`/var/lib/libvirt-backup-system/temp-restore/<uuid>-<timestamp>/`, NIC MACs
dropped so libvirt generates new ones, graphics devices switched to
autoport, host-passthrough `<hostdev>` devices removed, and UEFI nvram
relocated into the clone's own directory. The clone is always started after
it is defined.

Safety: the original VM and the backups are only read, never written; one
clone per restore point (restoring the same point again is refused until
`remove`); `remove` refuses to undefine a domain whose UUID no longer
matches the recorded clone UUID. See `temp-restore --help` for the full
contract.

## Cross-host recovery

`list-restore-points` walks every host directory under `BACKUP_PATH`, not
just the current `HOST_ID`, so a recovery host that mounted the backup tree
sees every host's snapshots. `restore` follows the same path: it picks up
the snapshot from whichever host's repo contains a matching `(uuid,
timestamp)`. When that host does not match the local one (or no local VM
with that UUID exists), the turnkey define path runs.

The shared token decrypts every host's repo, so cross-host restore is the
same command as same-host restore unless duplicate restore points require a
`--host-id` or `--run-id` disambiguator.

## How snapshots are tagged

`restore` resolves a meta snapshot by `(vm-uuid, timestamp)`, reads the
manifest, then looks up each disk snapshot by `run-id + disk=<target>`.
See [Kopia operations](kopia.md#tag-schema) for the full tag schema,
including consistency metadata.
