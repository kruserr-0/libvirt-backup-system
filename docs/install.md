# Install and prerequisites

## Prerequisites

The CLI shells out to `virsh`, `qemu-nbd`, `nbdcopy`, `qemu-img`, `df`, and
`kopia`. The installer takes care of `kopia` itself (see
[Bundled binary install](#bundled-binary-install) below); the rest come from
OS packages. `install` detects the Debian/Ubuntu release, checks the
dependencies **before touching anything**, and either apt-installs them with
your explicit consent (sudo is used only for the apt-get commands) or aborts
cleanly with a copy-paste command for your release — it never installs OS
packages without consent and never side-loads packages built for a different
release. `check` re-validates the same binaries on every run, including that
each one actually executes.

See [System dependencies](system-deps.md) for the full behavior, the
`-y` / `--non-interactive` flags, and the per-release apt commands:

```sh
sudo apt-get update
sudo apt-get install -y libvirt-clients qemu-utils libnbd-bin
```

## Install

On the first host, run `install` with the shared `BACKUP_PATH`. If no
`--kopia-password*` flag is supplied and no password file exists yet, the
installer generates a shared Kopia token, writes it to the secure password
file, and creates this host's repo with that token:

```sh
sudo env BACKUP_PATH=/home/admin/pro/vms/backups python3 -m libvirt_backup_system install
```

`BACKUP_PATH` must be a mounted shared filesystem (NFS) by default
(`BACKUP_REQUIRE_NFS_MOUNT=true`): preflight and every filesystem mutation
verify the mount is actually live so a dropped mount can never silently send
backups to the local disk. Preflight also verifies every node's `/etc/fstab`
entry for the backup mount matches the entry recorded by the first node —
same NFS server address, fstype, and options
(`BACKUP_REQUIRE_FSTAB_CONSISTENCY`; if the server address changes, update
`/etc/fstab` on ALL nodes, remount, and run `push-config` on one node).
For an intentionally local backup directory set
`BACKUP_REQUIRE_NFS_MOUNT=false`; see the
[configuration reference](env-vars.md).

Save the generated token in a password manager:

```sh
sudo libvirt-backup-system show-token
```

To join another host later, run `sudo libvirt-backup-system add-node` on an
installed host — it prints a pasteable install command carrying the same
`BACKUP_PATH` and token. See [Joining additional hosts](joining-hosts.md).

Operators who need to provide their own token can still use the explicit
password flags. These paths still require `--acknowledge-password-loss` before
the first write:

```sh
sudo env BACKUP_PATH=/home/admin/pro/vms/backups KOPIA_PW="$PW" \
  python3 -m libvirt_backup_system install --kopia-password-env=KOPIA_PW \
  --acknowledge-password-loss
```

For operators who do not want the explicit token in `ps`/journald, pipe it in:

```sh
echo -n "$PW" | sudo python3 -m libvirt_backup_system install --kopia-password-file=- \
  --acknowledge-password-loss
```

Or use an env var:

```sh
sudo env KOPIA_PW="$PW" python3 -m libvirt_backup_system install --kopia-password-env=KOPIA_PW \
  --acknowledge-password-loss
```

Behavior:

- First install with no password flag and no existing password file: generates
  a shared token, writes it to `/etc/libvirt-backup-system/kopia.pw` mode 600
  root-owned, creates the local repo at `BACKUP_PATH/<host-id>/kopia-repo/`,
  applies the global retention/compression policy, registers systemd units.
- First install with an explicit `--kopia-password*` value: writes the supplied
  value only when `--acknowledge-password-loss` is present. Refusal messages do
  not print the value, so store the exact token before running install.
- Re-running with the same token: idempotent.
- Re-running with a different token: hard fail. Use
  `libvirt-backup-system change-password` to rotate.
- Re-running with no password flag and an existing password file: keeps using
  the existing file (useful for refreshing systemd units without re-typing).
- Joining with the wrong token fails when peer repos already exist under
  `BACKUP_PATH`, because the installer cannot decrypt them with the supplied
  value.

Or install with an explicit token first, edit the environment file, initialize
the repo from that file, and then validate the schedules:

```sh
sudo python3 -m libvirt_backup_system install --kopia-password=<value> \
  --acknowledge-password-loss
sudoedit /etc/libvirt-backup-system/libvirt-backup.env
sudo libvirt-backup-system start
sudo libvirt-backup-system check
sudo libvirt-backup-system doctor
```

The first install leaves `BACKUP_PATH` blank unless it is supplied in the
environment. `BACKUP_REQUIRE_NFS_MOUNT` is also honored during first install
so operators can opt out of the (default-on) mount-point preflight in the
same copy-paste command, e.g. for a local test directory.
Other keys (for example `HOST_ID` and `KOPIA_COMPRESSION`) are written as
commented defaults in `libvirt-backup.env` and are silently ignored at install
time, because the systemd unit only reads the env file. Set those by editing
`libvirt-backup.env`; run `start` after changing values that affect unit
rendering, such as `BACKUP_PATH` or `SYSTEMD_ON_CALENDAR`.

When `BACKUP_PATH` is blank, systemd unit installation is skipped. Run `start`
after setting it so the service gets the matching `RequiresMountsFor=`
dependency and the timers are activated.

The installer creates:

- `/opt/libvirt-backup-system`
- `/usr/local/bin/libvirt-backup-system`
- `/etc/libvirt-backup-system/libvirt-backup.env`
- `/etc/libvirt-backup-system/kopia.pw` (mode 600 root-owned)
- `/usr/share/fish/vendor_completions.d/libvirt-backup-system.fish`
- `/usr/share/bash-completion/completions/libvirt-backup-system`
- `/var/lib/libvirt-backup-system/kopia-configs/` (per-repo Kopia config
  files)
- `/var/cache/libvirt-backup-system/kopia/` (Kopia chunk cache)

When `BACKUP_PATH` is configured, it also creates:

- `/etc/systemd/system/libvirt-backup-system.service`
- `/etc/systemd/system/libvirt-backup-system-check.service`
- `/etc/systemd/system/libvirt-backup-system.timer`
- `/etc/systemd/system/libvirt-backup-system-maintenance.service`
- `/etc/systemd/system/libvirt-backup-system-maintenance.timer`
- `/etc/systemd/system/libvirt-backup-system-maintenance-full.service`
- `/etc/systemd/system/libvirt-backup-system-maintenance-full.timer`
- `/etc/systemd/system/libvirt-backup-system-verify.service`
- `/etc/systemd/system/libvirt-backup-system-verify.timer`

### Shell completion

Bash and fish completion files are auto-installed by `install` and removed
by `uninstall`. Fish picks its file up from
`/usr/share/fish/vendor_completions.d/` without any `source` line in
`config.fish`; bash loads its file on first TAB from
`/usr/share/bash-completion/completions/` via the standard bash-completion
package (which also makes `sudo libvirt-backup-system <TAB>` work). TAB at
any subcommand position offers the available subcommands and flags in both
shells; bash completes values bare since it cannot render the descriptions
fish shows. Both files ship as package data inside `libvirt-backup-system`
itself — plain file copies, no PyPI access needed. If a shell is not
installed its file just sits unused.

#### Dynamic restore completion

`sudo libvirt-backup-system restore <TAB>` suggests the available VM UUIDs
with VM name, source host, and restore-point count in the description. After
picking a UUID, a second `<TAB>` lists the timestamps recorded for that VM so
the operator can pick the right run from the menu.

Completion caches `list-restore-points` output under
`$XDG_CACHE_HOME/libvirt-backup-system/restore-points.tsv` or
`~/.cache/libvirt-backup-system/restore-points.tsv`. The first cache fill runs
`sudo -n libvirt-backup-system list-restore-points`, so completion never
prompts for a password mid-TAB: it relies on the sysadmin's active sudo token.
Cache younger than five seconds is reused immediately; older cache refreshes
synchronously so a just-finished backup appears in the next completion menu.
If refresh fails, the last cache is used. When the token has lapsed and no
cache exists, completion falls back to a non-sudo invocation; on a default
install where the env file is mode `0600 root:root` that fallback produces no
rows.

The default backup timer is controlled by `SYSTEMD_ON_CALENDAR=*-*-* 02:30:00`.
`install` writes the units when `BACKUP_PATH` is already configured, but
does not enable the timers. `start` re-renders the units from the current
environment file, reloads systemd, and enables/starts
`libvirt-backup-system.timer`, `libvirt-backup-system-maintenance.timer`,
`libvirt-backup-system-maintenance-full.timer`, and
`libvirt-backup-system-verify.timer` after `check` has passed. Activating
the timers does not run a backup immediately;
the next backup waits for the configured schedule unless you run
`sudo libvirt-backup-system run`. The Kopia maintenance and verify timers
use staggered activation-relative initial delays so quick maintenance, full
maintenance, and verify do not all contend for the local repo as soon as the
timers are started.

When the systemd units are installed, `sudo libvirt-backup-system run` and
`sudo libvirt-backup-system check` dispatch through the corresponding
`.service` unit (via `systemctl start --wait`) so the ad-hoc invocation runs
in the exact environment the scheduled timer uses — same `EnvironmentFile=`,
`RequiresMountsFor=`, `StateDirectory=`, and hardening directives. The unit's
output is replayed to the operator's terminal by filtering the journal on the
run's `InvocationID`.

For `run`, the backup timer must already be loaded, enabled, and active. If a
systemd host has not successfully run `start`, manual `run` exits nonzero
with a "backup service is not running" error instead of starting an
unregistered ad-hoc backup.

Dispatch is automatically skipped for `check`/`preflight` (the subcommand
runs in-process instead) when any of these hold:

- `--prefix` is passed (install rooted elsewhere)
- `--config` is passed (the unit's `ExecStart` has the config path baked in)
- The unit file is not on disk yet (fresh checkout, package not deployed)
- `systemctl` is unavailable on the host
- `INVOCATION_ID` is set in the environment (systemd is already running the
  unit, so the orchestrator does not recurse)
- `LIBVIRT_BACKUP_NO_SYSTEMD_DISPATCH=1` is set in the environment

## Multi-host cutover

When deploying to a fleet from a previous (non-kopia) install, see the
[Kopia repo doc](kopia.md#multi-host-cutover) for the per-host checklist.

### Bundled binary install

On first install the orchestrator uses the vendored pinned `kopia` tarball
under `libvirt_backup_system/vendor/kopia/`, sha256-verifies it against the
constant in `libvirt_backup_system/kopia_vendor.py`, and installs it via an
atomic move to `/usr/local/bin/kopia`. If the vendored tarball is absent, it
falls back to downloading the same pinned upstream release asset. The step is
idempotent: if `kopia --version` already reports the pinned version, the
install skips unnecessary work. Bumping the pinned version is a deliberate
operator action — the matching sha256 and vendored artifact must be refreshed
in the same commit.

Kopia's automatic update checking is disabled everywhere: every managed
invocation runs with `KOPIA_CHECK_FOR_UPDATES=false`, and repositories are
created/connected with `--no-check-for-updates` so the setting persists into
the connection config — even a manual `kopia --config-file=...` run against a
managed repo never phones home or self-updates and later breaks the pinned
setup.

`nbdcopy` and the libvirt/qemu tooling are deliberately **not** bundled:
they come from the host OS's own package repositories via the dependency
check in [System dependencies](system-deps.md).

After installing this project with `BACKUP_PATH` configured, run
`sudo libvirt-backup-system check` to confirm every required binary resolves
before relying on scheduled backups. If you set `BACKUP_PATH` after install,
run `sudo libvirt-backup-system start` first so the local Kopia repo exists.

For filesystem-consistent VM snapshots, install and enable QEMU guest agent
inside each guest and confirm the libvirt guest-agent channel exists. Backups
still run without it, but those restore points are recorded as
crash-consistent. See [Backup consistency](backup-consistency.md) for the
guest setup and application freeze-hook guidance.

### Offline / air-gapped install

When outbound HTTPS to `github.com` is not available and the vendored kopia
tarball is absent, pre-place the binary by hand and the installer will detect
it and skip the bundled-install step:

```sh
# kopia: download elsewhere, verify the upstream sha256, then copy to
# /usr/local/bin/kopia (mode 0755 root-owned) at the version the
# installer pins.
sudo install -m 0755 -o root -g root /path/to/kopia /usr/local/bin/kopia
kopia --version
```

The OS-package dependencies (`libvirt-clients`, `qemu-utils`, `libnbd-bin`)
must come from your local mirror:

```sh
sudo apt-get install -y libvirt-clients qemu-utils libnbd-bin
nbdcopy --version
```

With everything present and runnable, `sudo libvirt-backup-system install`
proceeds straight to the token + repo + systemd-unit setup.

## Uninstall

```sh
sudo libvirt-backup-system uninstall
```

Uninstall removes installed program files and systemd units. If
`/opt/libvirt-backup-system` is itself a Git checkout, that checkout is
preserved. Config, state, and logs are preserved unless their matching
`--purge-*` flags are passed. The Kopia password file and repo directory under
`BACKUP_PATH` are never touched by uninstall. If `KOPIA_PASSWORD_FILE` is
configured inside any purged config, state, or log path, uninstall preserves
that file and the parent directories needed to keep it in place; delete it by
hand once the operator is sure the backups are no longer needed.
