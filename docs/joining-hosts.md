# Joining additional hosts

Every host writes to its own Kopia repo under the same `BACKUP_PATH`, and all
repos share one token. The first host can generate that token automatically;
additional hosts should join with the exact same value so `list-restore-points`
and `restore` can read every peer repo.

## First host

Run install with the shared backup path:

```sh
sudo env BACKUP_PATH=/home/admin/pro/vms/backups libvirt-backup-system install
sudo libvirt-backup-system check
sudo libvirt-backup-system start
```

When no password file exists and no `--kopia-password*` flag is supplied,
`install` generates the shared token and stores it in
`/etc/libvirt-backup-system/kopia.pw` mode 600 root-owned.

Save the token in a password manager:

```sh
sudo libvirt-backup-system show-token
```

`show-token` prints the raw secret. Avoid leaving it in logs or shell history.

## New host

Before joining, mount the shared backup storage on the new host **the same
way the existing nodes mount it**: the same NFS export from the same server
address, at the same path, with the same `/etc/fstab` options. The simplest
way is to copy the fstab line from a working node. Preflight records the
first node's fstab entry in the backup tree and fails on any node whose
fstab disagrees (see `BACKUP_REQUIRE_FSTAB_CONSISTENCY` in the
[configuration reference](env-vars.md)).

On an already installed host, print the join command. If the config changed
since it was last published, run `update-config` first so the new host
inherits the current settings instead of stale ones (see
[`update-config` in depth](update-config.md)):

```sh
sudo libvirt-backup-system update-config   # publish current settings
sudo libvirt-backup-system add-node        # then print the join command
```

The output is a pasteable command in this shape:

```sh
  sudo env BACKUP_PATH=... KOPIA_PW=... python3 -m libvirt_backup_system install --kopia-password-env KOPIA_PW --acknowledge-password-loss
```

The command is printed with two leading spaces on purpose: pasting it into
bash with `HISTCONTROL=ignorespace`/`ignoreboth` (the Debian/Ubuntu default)
or into fish keeps the embedded token out of shell history.

Run that command on the new KVM host from a checkout of this project. It uses
the same shared token but creates a separate repo for the new host:

```text
BACKUP_PATH/
  <existing-host-id>/kopia-repo/
  <new-host-id>/kopia-repo/
```

Then activate and validate schedules on the new host:

```sh
sudo libvirt-backup-system start
sudo libvirt-backup-system check
sudo libvirt-backup-system doctor
```

If `check` or `start` says the host is not joined or cannot open an existing
peer repo, run `sudo libvirt-backup-system add-node` on an already joined host
and paste the printed install command on this host.

## Shared configuration

The env file is shared across hosts through the backup tree so a new host
inherits the cluster's settings instead of starting from defaults. A single
seed file lives next to the per-host repos:

```text
BACKUP_PATH/
  libvirt-backup.env          # shared config seed
  <existing-host-id>/kopia-repo/
  <new-host-id>/kopia-repo/
```

The seed is a **template, not a live-synced file**:

- The first host publishes it automatically — during `install` when
  `BACKUP_PATH` is set, and on `start`. It is written only when no seed exists
  yet, so it is never silently overwritten.
- A joining host pulls the seed as its initial local config, inheriting
  retention, splitter, compression, NFS policy, and the backup schedule
  without re-typing them. The host's own install-time `BACKUP_PATH` still wins
  over the seed's recorded value.
- After joining, the local config is **independent**. Edit
  `/etc/libvirt-backup-system/libvirt-backup.env` and run `start` to change
  only that host (its own backup timer, mount path, etc.) — the seed is not
  touched.

`HOST_ID` is never shared: it scopes the per-host repo
(`BACKUP_PATH/<HOST_ID>/kopia-repo/`), so each node keeps its own (falling
back to `/etc/machine-id`).

### Updating the shared config

**Run `update-config` after every config change.** The workflow for any edit
is always the same three commands:

```sh
sudoedit /etc/libvirt-backup-system/libvirt-backup.env
sudo libvirt-backup-system start          # 1. apply the change locally
sudo libvirt-backup-system update-config  # 2. publish it for the cluster
```

Skipping `update-config` leaves the seed stale, so the next host you join
inherits the old settings and silently diverges from the nodes you already
fixed. `update-config` overwrites the seed (last writer wins) and also
re-records the shared fstab entry for the backup mount. It only affects
hosts that join *after* it runs; already-joined hosts keep their independent
config. Full details: [`update-config` in depth](update-config.md).

## Mount consistency across nodes

Alongside the config seed, the first node records its `/etc/fstab` entry for
the backup mount into `BACKUP_PATH/libvirt-backup-mounts.json`. Preflight on
every node then verifies:

- the local fstab entry matches the recorded one — same upstream server
  (same IP/export), same filesystem type, same mount options; and
- the live mount (`/proc/self/mounts`) matches fstab, so an edited but
  not-yet-remounted fstab is caught.

A mismatch fails preflight before any backup is attempted, and the error
prints both fstab entries so the operator can inspect them or simply copy
the fstab line from a working, already-joined node.

**Changing the NFS server address** (new IP, migrated NAS): update
`/etc/fstab` on ALL nodes, remount, then run `update-config` on one node —
it re-records the shared fstab entry along with the config seed. The check
is controlled by `BACKUP_REQUIRE_FSTAB_CONSISTENCY` (enabled by default).

## Wrong token behavior

If peer repos already exist under `BACKUP_PATH` and the new host is installed
with the wrong token, install fails because it cannot decrypt the existing
repos. Fix the token and rerun the printed `add-node` command; do not rotate
tokens unless you intentionally want to change the shared token for every
host.
