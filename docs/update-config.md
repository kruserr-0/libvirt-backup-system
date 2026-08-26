# `update-config`

Publishes this host's `/etc/libvirt-backup-system/libvirt-backup.env` to the
backup tree as the shared config seed (`BACKUP_PATH/libvirt-backup.env`),
overwriting any previous seed, and re-records this host's `/etc/fstab` entry
for the backup mount into the shared mount metadata
(`BACKUP_PATH/libvirt-backup-mounts.json`).

## Run it after every config change

**Whenever you change the config, finish with `update-config`.** The full
workflow after any edit is always the same three commands:

```sh
sudoedit /etc/libvirt-backup-system/libvirt-backup.env
sudo libvirt-backup-system start          # 1. apply the change locally
sudo libvirt-backup-system update-config  # 2. publish it for the cluster
```

Skipping `update-config` leaves the shared seed stale: the next host you
join with `add-node` inherits the **old** settings (old retention, old
schedule, old NFS policy), silently diverging from the hosts you already
fixed. Publishing after every change keeps "what a new node gets" identical
to "what the working nodes run".

## What it updates

1. **The shared config seed.** A node joining the same `BACKUP_PATH` (via
   the `add-node` command) pulls the seed as its initial local config, so it
   inherits retention (`KEEP_*`), splitter, compression, the backup
   schedule, and the NFS/fstab policy without re-typing them. Last writer
   wins: the most recent `update-config` from any host becomes the
   template. `HOST_ID` is never shared — it scopes the per-host repo, so
   each node keeps its own (falling back to `/etc/machine-id`).

2. **The shared fstab entry** for the backup mount, used by the
   `BACKUP_REQUIRE_FSTAB_CONSISTENCY` preflight check. This makes
   `update-config` the documented final step after deliberately changing
   the NFS server address:

   ```sh
   # on EVERY node: point fstab at the new server and remount
   sudoedit /etc/fstab
   sudo umount /mnt/qnap-backups && sudo mount /mnt/qnap-backups

   # then on ONE node: re-record the shared entry
   sudo libvirt-backup-system update-config
   ```

   Until `update-config` runs, preflight fails on every node whose fstab no
   longer matches the previously recorded entry — that is the guard working
   as intended, not a bug.

## What it does NOT do

- It does **not** push config to already-joined hosts. After joining, each
  host's local config is independent; editing host A and running
  `update-config` changes nothing on host B. To roll a change across the
  fleet, edit the env file and run `start` on **each** host (and finish
  with `update-config` on one of them so future joins match).
- It does not restart or reschedule anything — that is `start`'s job, which
  is why `start` comes first in the workflow above.
- It does not touch backups, repos, or the shared kopia token.

## Relationship to `add-node`

`add-node` hands a new host the shared token and `BACKUP_PATH`; the seed
published by `update-config` hands it everything else. Before joining a new
host, make sure the seed reflects reality:

```sh
sudo libvirt-backup-system update-config   # publish current settings
sudo libvirt-backup-system add-node        # then print the join command
```

See [Joining additional hosts](joining-hosts.md) for the full join flow and
[Mount consistency](joining-hosts.md#mount-consistency-across-nodes) for the
fstab requirements on the joining host.
