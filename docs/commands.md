# Command reference

## `install`

Installs the package copy, wrapper script, config file, bash/fish
completions, and systemd units when `BACKUP_PATH` is configured; writes the
shared kopia token to `/etc/libvirt-backup-system/kopia.pw` (mode 600) and
creates the local repo at `BACKUP_PATH/<host-id>/kopia-repo/` with the
retention/compression policy applied. It first gates on the system
dependencies (see [System dependencies](system-deps.md)): interactively it
offers to apt-install what is missing, otherwise it aborts before modifying
anything and prints a copy-paste command; `--non-interactive` never prompts.

```sh
sudo env BACKUP_PATH=/home/admin/pro/vms/backups libvirt-backup-system install
sudo libvirt-backup-system install --kopia-password=<value> --acknowledge-password-loss
sudo libvirt-backup-system install --kopia-password-file=/path/to/file --acknowledge-password-loss
echo -n "$PW" | sudo libvirt-backup-system install --kopia-password-file=- --acknowledge-password-loss
sudo env KOPIA_PW=... libvirt-backup-system install --kopia-password-env=KOPIA_PW --acknowledge-password-loss
```

With no `--kopia-password*` flag and no password file, `install` generates the
shared token automatically. Explicit first writes still require
`--acknowledge-password-loss`. Re-running install is idempotent with the same
token and fails on mismatch; joining with the wrong token also fails when peer
repos already exist.

When `BACKUP_PATH` is set, a fresh install also syncs the shared config at
`BACKUP_PATH/libvirt-backup.env`: the first node publishes, a joining node
pulls it. See [`push-config` / `pull-config`](#push-config--pull-config).

## `add-node`

Prints a pasteable command joining another host to this backup set:

```sh
sudo libvirt-backup-system add-node
```

```sh
  sudo env BACKUP_PATH=... KOPIA_PW=... python3 -m libvirt_backup_system install --kopia-password-env KOPIA_PW --acknowledge-password-loss
```

The two leading spaces are intentional: pasting into bash
(`HISTCONTROL=ignoreboth`, the Debian/Ubuntu default) or fish keeps the
token out of history. The joining host must mount the same NFS export (same
server IP, path, fstab options) — see
[Mount consistency](joining-hosts.md#mount-consistency-across-nodes).

## `show-token`

Prints the raw shared token from the secure password file:

```sh
sudo libvirt-backup-system show-token
```

## `push-config` / `pull-config`

**Config changes flow through the shared NFS tree with an explicit
push/pull pair** — push on the node you edited, pull on every other node
(`update-config` is a deprecated alias of `push-config`):

```sh
# on the node you edited:
sudoedit /etc/libvirt-backup-system/libvirt-backup.env
sudo libvirt-backup-system start        # 1. apply locally
sudo libvirt-backup-system push-config  # 2. publish for the cluster

# on every other node:
sudo libvirt-backup-system pull-config  # 3. take over the shared config
sudo libvirt-backup-system start        # 4. apply locally
```

Skipping `push-config` leaves the shared config stale for pulls and future
joins; nothing is live-synced. `pull-config` preserves the local `HOST_ID` and
`BACKUP_PATH`; `push-config` also re-records the shared fstab entry. Full
details: [`push-config` / `pull-config`](config-sync.md).

## `change-password`

Rotates the kopia repo token on this host. Read the current token, verify it
decrypts the local repo, run `kopia repository change-password` to rewrap the
master key, atomically replace the password file.

```sh
sudo libvirt-backup-system change-password --new-kopia-password=<value>
sudo libvirt-backup-system change-password --new-kopia-password-file=/path
echo -n "$PW" | sudo libvirt-backup-system change-password --new-kopia-password-file=-
sudo env NEW_KOPIA_PW=... libvirt-backup-system change-password --new-kopia-password-env=NEW_KOPIA_PW
```

Run the same command on every host, in any order; each host rotates its own
repo independently, and `doctor` flags any host still holding the old value.
See [Kopia password handling](kopia-password.md#password-rotation).

Kopia rotation receives the resolved new value in Kopia's argv; avoid
running it where untrusted users can inspect process arguments.

## `uninstall`

Removes installed program files and systemd units. A Git checkout at
`/opt/libvirt-backup-system` is preserved. Config, state, logs, the kopia
password file, and the on-disk repo are preserved by default. The purge flags
only remove config, state, and logs; uninstall never removes the Kopia
password file or repo. If `KOPIA_PASSWORD_FILE` is configured inside any
purged config, state, or log path, uninstall preserves that file and the parent
directories needed to keep it in place.

```sh
sudo libvirt-backup-system uninstall
sudo libvirt-backup-system uninstall --purge-config --purge-state --purge-logs
```

## `check` / `preflight`

Validates config, binaries, root policy, VM discovery, backup path
writability, the password file, local repo connectivity, and free space.
Run `start` once after setting a new `BACKUP_PATH`; `check` expects the repo.

```sh
sudo libvirt-backup-system check
```

## `doctor`

Diagnoses install registration, runtime state, and the same preflight
surface that `check` covers. Specifically, `doctor` is a superset of
`check` — it runs the full preflight layer and then appends:

- Wrapper script, package directory, and config file are in place.
- All systemd unit and timer files exist on disk with content matching what a
  fresh `install` would render (catches drift after editing the env file
  without re-running install).
- Backup, maintenance, full-maintenance, and verify timers are enabled and
  active.
- Last `libvirt-backup-system.service` run completed cleanly.
- Local kopia repo connects with the shared token and
  `kopia repository status` is clean.
- Local `kopia maintenance info` and a lightweight
  `kopia snapshot verify --verify-files-percent=0` complete cleanly.
  `doctor` uses `maintenance info` because Kopia does not expose a
  non-mutating `maintenance run --dry-run`; the scheduled timers run the
  actual maintenance commands.
- Every peer repo under `BACKUP_PATH/*/kopia-repo/` is reachable read-only
  with the shared token (cross-host-restore smoke test).

```sh
sudo libvirt-backup-system doctor
```

## `start`

Installs or refreshes the systemd unit files from the current environment
file, reloads systemd, refreshes the kopia global retention/compression
policy, and enables/starts `libvirt-backup-system.timer`,
`libvirt-backup-system-maintenance.timer`,
`libvirt-backup-system-maintenance-full.timer`, and
`libvirt-backup-system-verify.timer`. Activates the schedules only; does
not run a backup immediately. Kopia maintenance and verify timers have
staggered activation-relative initial delays to avoid concurrent first-run
repo operations.
Use after `install`, after editing `/etc/libvirt-backup-system/libvirt-backup.env`,
and to initialize an empty `BACKUP_PATH`; then run `check`.

On the first node, `start` also publishes the shared config when none
exists yet (never overwriting an existing one); later edits travel via
[`push-config` / `pull-config`](#push-config--pull-config).

```sh
sudo libvirt-backup-system start
```

## `run` / `backup`

Runs preflight, acquires the run lock, and backs up every running VM.
Offline VMs are logged as `skipping vm because it is offline` and skipped.
Each VM produces one kopia disk snapshot per disk plus one meta snapshot with
the run manifest and restore-point tags (`vm-name`, `timestamp`,
`consistency`). QEMU guest agent quiesce is attempted per VM and falls back to
a crash-consistent snapshot if quiesce is unavailable; see
[Backup consistency](backup-consistency.md).

Manual backups require the systemd schedule to have been activated first
with a successful `start`. On a systemd host, `run`/`backup` exits nonzero with a
"backup service is not running" error instead of starting an ad-hoc backup
when the service/timer has not been installed and activated.

On a systemd host the backup runs **in the background**: `run`/`backup`
dispatches the work to the `libvirt-backup-system.service` unit with
`systemctl start --no-block` and returns as soon as systemd accepts the job.
The backup then runs under systemd (PID 1), so it keeps running to completion
even if you log out, close the terminal, or drop the SSH session. Follow it
with `libvirt-backup-system log -f` and review past runs with
`libvirt-backup-system log`. When systemd is unavailable (or `--config` /
`--prefix` is set, or you are already inside the unit), the backup instead
runs in-process in the foreground.

Pruning is handled by the kopia maintenance timer, not by the backup loop —
a slow GC pass cannot delay backups.

```sh
sudo libvirt-backup-system run            # starts in the background
sudo libvirt-backup-system backup
sudo libvirt-backup-system log -f         # follow the running backup
```

## `log` / `logs`

Shows the systemd journal for the backup units, modeled on `docker logs`. By
default it prints the most recent 50 lines from `libvirt-backup-system.service`
(the backup orchestrator) and exits. Pass `-f`/`--follow` to keep the stream
open and print new lines as the background backup writes them — the same live
output a foreground run would show. Following is read-only: Ctrl-C stops the
journal tail, not the backup.

```sh
sudo libvirt-backup-system log            # last 50 lines of the backup run
sudo libvirt-backup-system log -f         # stream live, like docker logs -f
sudo libvirt-backup-system log -n 200     # last 200 lines
sudo libvirt-backup-system log -n all     # entire run history
sudo libvirt-backup-system log -f all     # follow backup + maintenance + verify
sudo libvirt-backup-system log verify     # the kopia verify unit's journal
```

- `-f`, `--follow` — stream new lines instead of exiting.
- `-n N`, `--lines N` — recent lines to show before following; a non-negative
  integer or `all`. Default: `50`.
- Trailing component (default `run`) picks which unit's journal to read:
  `run`, `check`, `maintenance`, `maintenance-full`, `verify`, or `all` (the
  backup, maintenance, full-maintenance, and verify units interleaved).

`log` reads the system journal, so it needs the same privileges as
`journalctl` (run under `sudo`, or as a member of `systemd-journal`/`adm`).

## `status`

Prints `systemctl status` for the backup timer/service, check service,
maintenance timer/service, full-maintenance timer/service, and verify
timer/service units. Output is the raw human-readable systemctl output (not
JSON), so the next-fire time, last-run result, and any recent journal lines
are visible at a glance. Exit code is the
worst (highest) systemctl return code across those units, so unloaded units
propagate as failure.

```sh
sudo libvirt-backup-system status
```

## `list-vms`

Lists selected VMs after applying `VM_BLACKLIST` (UUID-based).

```sh
sudo libvirt-backup-system list-vms
sudo libvirt-backup-system list-vms --json
sudo libvirt-backup-system list-vms --include-blacklisted
```

## `verify`

Runs `kopia snapshot verify` against the local repo by default. Cross-host
verification is opt-in via `--include-hosts`.

```sh
sudo libvirt-backup-system verify
sudo libvirt-backup-system verify --include-hosts=host-a,host-b
```

`VM_BLACKLIST` is intentionally ignored: blacklisted VMs' backups still verify.

## `list-restore-points` / `restore` / `temp-restore`

`list-restore-points` lists every restorable backup run across all hosts;
`restore` restores one by overwriting the live VM (destructive; guarded by
confirmation and a pre-restore safety backup); `temp-restore` boots the
backup as a throwaway clone beside the untouched original instead. See
[Restore commands](restore.md) and each subcommand's `--help`.

## `du`

Shows backup usage. With no filters it reports actual filesystem usage for
each `BACKUP_PATH/<host>/kopia-repo/` and a total.

```sh
sudo libvirt-backup-system du
sudo libvirt-backup-system du host-a
sudo libvirt-backup-system du host-a aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa
```

One drilldown argument is a host id or VM UUID; two are host id then VM UUID.
Drilldowns report restore-point count, latest logical VM size, latest
consistency, and Kopia packed size; top-level remains physical repo usage.

## Retention

Retention is enforced by the kopia global policy
(`KEEP_LATEST/HOURLY/DAILY/WEEKLY/MONTHLY/ANNUAL`), refreshed from the env
file on every `start`; see [Kopia operations](kopia.md#maintenance) for the
maintenance timers that prune and compact the repos.
