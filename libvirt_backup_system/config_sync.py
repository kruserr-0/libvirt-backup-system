"""Share the env config across hosts through the backup tree.

A single "shared config" file lives at ``BACKUP_PATH/<SHARED_CONFIG_NAME>``,
alongside the per-host ``BACKUP_PATH/<host-id>/kopia-repo/`` directories:

* ``push-config`` publishes this host's config there (the first node also
  publishes automatically on ``install``/``start``).
* ``pull-config`` overwrites another node's local config from the shared
  file — push on one node, pull on the others to roll a config change
  across the fleet.
* A node *joining* an existing backup tree pulls the shared config as its
  initial local config, so it inherits retention, splitter, NFS policy,
  etc. without re-typing them.

The file is never live-synced: nothing changes on a node until it pulls.

``HOST_ID`` is deliberately blanked in the shared file: host identity scopes
the per-host repo (``BACKUP_PATH/<HOST_ID>/kopia-repo/``), so sharing it
would collide two hosts onto one repo. ``pull-config`` likewise preserves
the local ``HOST_ID`` and ``BACKUP_PATH``.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import mount_consistency
from .config import Config, parse_env_file
from .config_data import DEFAULTS
from .logging_json import event

SHARED_CONFIG_NAME = "libvirt-backup.env"


def shared_config_path(config: Config) -> Path | None:
    """Path of the shared seed under ``BACKUP_PATH``, or ``None`` if unset.

    ``BACKUP_PATH`` is an operator-supplied mount path used verbatim (it is not
    run through the install ``--prefix``), matching ``paths.backup_root`` and
    the per-host repo layout.
    """
    backup_path = config.get("BACKUP_PATH").strip()
    if not backup_path:
        return None
    return Path(backup_path) / SHARED_CONFIG_NAME


def _render_seed(source_path: Path) -> str:
    """Render the seed env text from a local env file.

    Re-renders through ``Config.render_env`` so the published file is the clean,
    fully-commented template form regardless of how the source was edited, and
    forces ``HOST_ID`` empty so joiners never inherit another host's identity.
    """
    values = dict(DEFAULTS)
    values.update(parse_env_file(source_path))
    values["HOST_ID"] = ""
    return Config(values=values, path=source_path, prefix=Path("/")).render_env()


def _atomic_write(dest: Path, content: str) -> None:
    # Temp-in-same-dir + atomic rename so a peer reading the seed over NFS never
    # observes a half-written file. 0600: the seed mirrors the local env, which
    # is root-owned 0600; backup-tree access is already root-on-every-host.
    tmp = dest.with_name(f".{dest.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(dest)


def _exclusive_write(dest: Path, content: str) -> bool:
    """Create ``dest`` only if absent. Returns ``False`` when it already exists.

    O_EXCL closes the race where two first-time installs target the same shared
    backup tree concurrently: the first writer wins, the rest see the seed and
    fall through to the join path instead of clobbering it.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(dest, flags, 0o600)
    except FileExistsError:
        return False
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    return True


def pull_shared_config_values(config: Config) -> dict[str, str] | None:
    """Parsed seed values for a joining node, or ``None`` when no seed exists.

    Returns the env key/value mapping (``HOST_ID`` is never present — it is
    blanked/commented in the seed) so the caller can overlay it onto the
    install-time config before rendering the local env file.
    """
    src = shared_config_path(config)
    if src is None or not src.exists():
        return None
    try:
        return parse_env_file(src)
    except OSError as exc:
        event("warning", "shared config unreadable; using defaults", path=str(src), error=str(exc))
        return None


def seed_shared_config(config: Config, source_path: Path) -> None:
    """Best-effort publish of the first node's config to the backup tree.

    Used by ``install``/``start``: writes the seed only if it does not already
    exist, so a node that joined later (and edited its own config) never
    clobbers the shared template. Failures are warnings, not errors — config
    sharing is a convenience and must not fail an otherwise-good install.
    """
    dest = shared_config_path(config)
    if dest is None:
        return
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _exclusive_write(dest, _render_seed(source_path)):
            event("info", "published shared config", path=str(dest))
    except OSError as exc:
        event("warning", "failed to publish shared config", path=str(dest), error=str(exc))


def push_shared_config(config: Config) -> int:
    """Overwrite the shared config with this host's current config.

    Backs the ``push-config`` command (``update-config`` is a deprecated
    alias). Unlike ``seed_shared_config`` this always replaces the shared
    file (last writer wins); other nodes take it over with ``pull-config``,
    and joining nodes inherit it automatically. Returns a process exit code.
    """
    dest = shared_config_path(config)
    if dest is None:
        event("error", "BACKUP_PATH is not configured; set it before push-config")
        return 1
    if not config.path.exists():
        event("error", "config file not found", path=str(config.path))
        return 1
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(dest, _render_seed(config.path))
    except OSError as exc:
        event("error", "failed to publish shared config", path=str(dest), error=str(exc))
        return 1
    event("info", "published shared config", path=str(dest))
    # Also re-record this host's fstab entry for the backup mount: after a
    # deliberate change (e.g. a new NFS server address rolled out to every
    # node), push-config is the documented way to refresh the shared entry
    # that BACKUP_REQUIRE_FSTAB_CONSISTENCY validates against.
    mount_consistency.record_local_mount(config, overwrite=True)
    return 0


def pull_local_config(config: Config) -> int:
    """Overwrite the local env file from the shared config (``pull-config``).

    Push on one node, pull on the others: everything in the shared file is
    taken over except the host-local keys — HOST_ID (identity scopes the
    per-host repo) and BACKUP_PATH (how this node reaches the shared storage
    in the first place). The operator must run ``start`` afterwards so the
    systemd units are re-rendered from the new values. Returns an exit code.
    """
    src = shared_config_path(config)
    if src is None:
        event("error", "BACKUP_PATH is not configured; set it before pull-config")
        return 1
    if not config.path.exists():
        event("error", "local config file not found; run install first", path=str(config.path))
        return 1
    if not src.exists():
        event("error", "no shared config found; run push-config on a configured node first", path=str(src))
        return 1
    try:
        seed_values = parse_env_file(src)
    except OSError as exc:
        event("error", "shared config unreadable", path=str(src), error=str(exc))
        return 1
    local_values = parse_env_file(config.path)
    values = dict(DEFAULTS)
    values.update(seed_values)
    # An empty local HOST_ID stays empty (= keep following /etc/machine-id)
    # rather than freezing the resolved id into the file; BACKUP_PATH falls
    # back to the resolved value when the file relied on the environment.
    values["HOST_ID"] = local_values.get("HOST_ID", "")
    values["BACKUP_PATH"] = local_values.get("BACKUP_PATH", config.get("BACKUP_PATH"))
    content = Config(values=values, path=config.path, prefix=config.prefix).render_env()
    try:
        _atomic_write(config.path, content)
    except OSError as exc:
        event("error", "failed to write local config", path=str(config.path), error=str(exc))
        return 1
    event("info", "pulled shared config", path=str(config.path), source=str(src))
    print(
        "\nPulled the shared config into "
        f"{config.path}.\nNow apply it on this host:\n  sudo libvirt-backup-system start\n",
        flush=True,
    )
    return 0
