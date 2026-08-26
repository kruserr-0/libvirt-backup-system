"""Help text for install / add-node / show-token / push-config / pull-config.

Split out of ``cli_help`` to keep both files under the project's 300-LOC
ceiling. ``cli_help`` re-exports these names so the parser wiring keeps a
single import surface.
"""

from __future__ import annotations

INSTALL_HELP = "Install the wrapper, config file, package copy, and systemd units."
INSTALL_DESCRIPTION = """\
Install libvirt-backup-system: copy the package to /opt/libvirt-backup-system,
write the /usr/local/bin/libvirt-backup-system wrapper, drop the default
/etc/libvirt-backup-system/libvirt-backup.env (preserving an existing one),
render the systemd unit files, lay down the shared kopia token at
KOPIA_PASSWORD_FILE, and install the bash and fish completion scripts. The
timers are NOT enabled automatically -- run ``start`` and then ``check``
after editing the env file to initialize the repo and validate the setup.

Before anything is installed, the system dependencies (virsh, qemu-nbd,
qemu-img, nbdcopy) are checked. When some are missing, install detects the
Debian/Ubuntu release and offers to install the matching apt packages after
confirmation (sudo asks for its password on the terminal and the credentials
are used only for the apt-get commands, then dropped); -y answers that
confirmation automatically. For automation, --non-interactive never prompts:
combined with -y it still apt-installs the missing packages when running as
root or with passwordless sudo, and skips the attempt otherwise. Declining,
--non-interactive without -y, a skipped attempt, or an unrecognized OS
release aborts the install before anything is modified, printing the exact
copy-paste apt command (and, on unknown releases, your detected OS version
plus a ready-made question to paste into Google or ChatGPT) -- this tool
never installs OS packages without consent. Dependencies already on the
system are never installed again; pass --reinstall-deps to force
``apt-get install --reinstall`` of every dependency package (and a re-extract
of the pinned kopia binary) when they are broken for whatever reason. kopia
itself is installed by this command at a pinned version with automatic
update checks disabled.

For a one-shot first install with BACKUP_PATH:

  sudo env BACKUP_PATH=/mnt/qnap-backups libvirt-backup-system install

When no password file exists and no --kopia-password* flag is supplied,
install generates the shared token automatically. Save it with ``show-token``;
print a pasteable command for the next host with ``add-node``. Explicit
--kopia-password* values are still accepted and require
--acknowledge-password-loss on first write. If peer repos already exist,
install validates that the token can decrypt them before creating this
host's repo."""


ADD_NODE_HELP = "Print a pasteable install command for joining another host."
ADD_NODE_DESCRIPTION = """\
Print a pasteable install command that joins another KVM host to this backup
set. The command embeds this host's BACKUP_PATH and the shared kopia token.

The printed command starts with two spaces on purpose: pasting it into bash
with HISTCONTROL=ignorespace/ignoreboth (the Debian/Ubuntu default) or into
fish keeps the embedded token out of shell history.

The joining host must reach the same backup storage as the existing nodes:
mount the same NFS export from the same server address at the same path,
with the same /etc/fstab options -- copy the fstab line from a working node
before pasting the install command. Preflight records the first node's
fstab entry in the backup tree and fails when a node's fstab disagrees (see
BACKUP_REQUIRE_FSTAB_CONSISTENCY in the env file). If you ever change the
NFS server address, update /etc/fstab on ALL nodes, remount, and run
``push-config`` on one node to re-record the shared entry.

The new host inherits its settings from the shared config. If you have
changed this host's config since it was last published, run ``push-config``
first so the joining host inherits the current settings instead of stale
ones:

  sudo libvirt-backup-system push-config   # publish current settings
  sudo libvirt-backup-system add-node      # then print the join command"""
SHOW_TOKEN_HELP = "Print the shared kopia token from the local password file."


PUSH_CONFIG_HELP = "Publish this host's config to the shared backup tree. Run after EVERY config change."
PUSH_CONFIG_DESCRIPTION = """\
Copy this host's /etc/libvirt-backup-system/libvirt-backup.env up to the
backup tree as the shared config (BACKUP_PATH/libvirt-backup.env),
overwriting any previous version, and re-record this host's /etc/fstab
entry for the backup mount in the shared mount metadata.

RUN THIS AFTER EVERY CONFIG CHANGE, then ``pull-config`` on the other
nodes. The fleet-wide workflow for any edit is always:

  # on the node you edited:
  sudoedit /etc/libvirt-backup-system/libvirt-backup.env
  sudo libvirt-backup-system start        # 1. apply the change locally
  sudo libvirt-backup-system push-config  # 2. publish it for the cluster

  # on every other node:
  sudo libvirt-backup-system pull-config  # 3. take over the shared config
  sudo libvirt-backup-system start        # 4. apply it locally

Skipping push-config leaves the shared config stale: other nodes pull OLD
settings, and the next host joined with ``add-node`` silently inherits them.
The shared config is never live-synced -- nothing changes on a node until
it pulls. The most recent push-config from any host wins. ``HOST_ID`` is
never shared -- it scopes the per-host repo, so each node keeps its own.
The first node also publishes automatically on ``install``/``start`` when no
shared config exists yet.

push-config is also the final step after deliberately changing the NFS
server address: update /etc/fstab on ALL nodes, remount, then run
push-config on one node to re-record the shared fstab entry that the
BACKUP_REQUIRE_FSTAB_CONSISTENCY preflight validates against.

``update-config`` is a deprecated alias of push-config."""


PULL_CONFIG_HELP = "Take over the shared config pushed by another node, then run ``start``."
PULL_CONFIG_DESCRIPTION = """\
Overwrite this host's /etc/libvirt-backup-system/libvirt-backup.env with the
shared config from the backup tree (BACKUP_PATH/libvirt-backup.env), as
published by ``push-config`` on another node. Everything is taken over
except this host's own identity and location: ``HOST_ID`` and
``BACKUP_PATH`` keep their local values.

Run this on every other node after a ``push-config``, and follow it with
``start`` so the systemd units are re-rendered from the new values:

  sudo libvirt-backup-system pull-config
  sudo libvirt-backup-system start

Fails cleanly when BACKUP_PATH is not configured, when this host has no
local config yet (run ``install`` first), or when no shared config has been
pushed yet."""
