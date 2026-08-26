"""Help text for install / add-node / show-token / update-config.

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
KOPIA_PASSWORD_FILE, and install the fish completion script. The timers are
NOT enabled automatically -- run ``start`` and then ``check`` after editing
the env file to initialize the repo and validate the setup.

Before anything is installed, the system dependencies (virsh, qemu-nbd,
qemu-img, nbdcopy) are checked. When some are missing, install detects the
Debian/Ubuntu release and offers to install the matching apt packages after
confirmation (sudo asks for its password on the terminal and the credentials
are used only for the apt-get commands, then dropped). Declining, running
with --non-interactive, or an unrecognized OS release aborts the install
before anything is modified, printing a copy-paste install command instead
-- this tool never installs OS packages without consent. kopia itself is
installed by this command at a pinned version with automatic update checks
disabled.

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
``update-config`` on one node to re-record the shared entry."""
SHOW_TOKEN_HELP = "Print the shared kopia token from the local password file."


UPDATE_CONFIG_HELP = "Publish this host's env config to the backup path as the shared seed for new joins."
UPDATE_CONFIG_DESCRIPTION = """\
Copy this host's /etc/libvirt-backup-system/libvirt-backup.env up to the
backup tree as the shared config seed (BACKUP_PATH/libvirt-backup.env),
overwriting any previous seed.

The shared config is a *seed*, not a live-synced file. The first node
publishes it automatically (during ``install`` when BACKUP_PATH is set, and on
``start``). When a new host joins (``install`` against the same BACKUP_PATH and
token, e.g. via ``add-node``), it pulls the seed as its initial local config so
it inherits retention, splitter, compression, and NFS policy without re-typing
them. After joining, the local config is independent: edit it and run
``start`` to change only this host (its own backup schedule, mount path, etc.)
without touching the seed.

Run ``update-config`` whenever you want this host's current config to become
the template that future joins inherit. The most recent ``update-config`` from
any host wins. ``HOST_ID`` is never shared -- it scopes the per-host repo, so
each node keeps its own (falling back to /etc/machine-id).

  sudoedit /etc/libvirt-backup-system/libvirt-backup.env
  sudo libvirt-backup-system start          # apply locally
  sudo libvirt-backup-system update-config  # publish for future joins"""
