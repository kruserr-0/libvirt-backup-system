# `push-config` / `pull-config`

## The model

**Config changes flow through the shared NFS tree with an explicit
push/pull pair.** `push-config` uploads this host's
`/etc/libvirt-backup-system/libvirt-backup.env` to
`BACKUP_PATH/libvirt-backup.env`; `pull-config` overwrites another host's
local env file from it. You push on the node you edited and pull on every
other node — nothing is ever live-synced, and a node never changes until it
pulls. (`update-config` is a deprecated alias of `push-config`.)

The workflow for **every** config change is always the same four steps:

```sh
# on the node you edited:
sudoedit /etc/libvirt-backup-system/libvirt-backup.env
sudo libvirt-backup-system start        # 1. apply locally
sudo libvirt-backup-system push-config  # 2. publish for the cluster

# on every other node:
sudo libvirt-backup-system pull-config  # 3. take over the shared config
sudo libvirt-backup-system start        # 4. apply locally
```

Skipping `push-config` leaves the shared config stale: other nodes pull
**old** settings, and the next host joined with `add-node` silently inherits
them, diverging from the hosts you already fixed. The shared file is never
live-synced — nothing changes on a node until it pulls.

## What `push-config` updates

1. **The shared config** at `BACKUP_PATH/libvirt-backup.env` (last writer
   wins). Nodes take it over with `pull-config`; a host joining via
   `add-node` inherits it automatically as its initial config (retention,
   splitter, compression, schedule, NFS/fstab policy).
2. **The shared fstab entry** for the backup mount, used by the
   `BACKUP_REQUIRE_FSTAB_CONSISTENCY` preflight check. This makes
   `push-config` the documented final step after deliberately changing the
   NFS server address:

   ```sh
   # on EVERY node: point fstab at the new server and remount
   sudoedit /etc/fstab
   sudo umount /mnt/qnap-backups && sudo mount /mnt/qnap-backups

   # then on ONE node: re-record the shared entry
   sudo libvirt-backup-system push-config
   ```

   Until `push-config` runs, preflight fails on every node whose fstab no
   longer matches the previously recorded entry — that is the guard working
   as intended, not a bug.

## What `pull-config` does (and preserves)

`pull-config` replaces the local env file with the shared one, **except**
the host-local keys, which keep their local values:

- `HOST_ID` — identity scopes the per-host repo
  (`BACKUP_PATH/<HOST_ID>/kopia-repo/`); sharing it would collide two hosts
  onto one repo. An empty local `HOST_ID` stays empty (keeps following
  `/etc/machine-id`).
- `BACKUP_PATH` — how this node reaches the shared storage in the first
  place.

Always follow a pull with `start` so the systemd units are re-rendered from
the new values. `pull-config` fails cleanly when `BACKUP_PATH` is not
configured, when the host has no local config yet (run `install` first), or
when nothing has been pushed yet.

## Relationship to `add-node`

`add-node` hands a new host the shared token and `BACKUP_PATH`; the shared
config pushed by `push-config` hands it everything else. Before joining a
new host, make sure the shared config reflects reality:

```sh
sudo libvirt-backup-system push-config   # publish current settings
sudo libvirt-backup-system add-node      # then print the join command
```

See [Joining additional hosts](joining-hosts.md) for the full join flow and
[Mount consistency](joining-hosts.md#mount-consistency-across-nodes) for the
fstab requirements on the joining host.
