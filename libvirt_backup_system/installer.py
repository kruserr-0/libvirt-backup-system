from __future__ import annotations

import os
from pathlib import Path

from . import config_sync, installer_deps, kopia_password, kopia_repo, mount_consistency, preflight
from .config import Config, default_config_path, prefixed, root_prefix
from .fish_completion import install_fish_completion
from .installer_binaries import BinaryInstallError, install_kopia
from .installer_helpers import INSTALL_TIME_ENV_KEYS
from .installer_helpers import install_backup_path_configured as _install_backup_path_configured
from .installer_helpers import install_package as _install_package
from .installer_helpers import install_without_backup_path as _install_without_backup_path
from .installer_helpers import log_dropped_install_time_env as _log_dropped_install_time_env
from .installer_helpers import print_install_next_steps as _print_install_next_steps
from .installer_helpers import render_units as _render_units
from .installer_helpers import write_initial_config as _write_initial_config
from .installer_helpers import write_wrapper as _write_wrapper
from .installer_password import install_password as _install_password
from .installer_uninstall import uninstall_locked as _uninstall_locked
from .lock import LockBusyError, acquire_run_lock
from .logging_json import event
from .shell import configure_default_timeout
from .systemd_templates import UNIT_SERVICE, UNIT_TIMER
from .systemd_units import (
    MAINTENANCE_FULL_TIMER_NAME,
    MAINTENANCE_FULL_UNIT_NAME,
    MAINTENANCE_TIMER_NAME,
    MAINTENANCE_UNIT_NAME,
    VERIFY_TIMER_NAME,
    VERIFY_UNIT_NAME,
    run_systemctl,
    systemctl_available,
    validate_systemd_path,
)

__all__ = ["INSTALL_TIME_ENV_KEYS", "UNIT_SERVICE", "UNIT_TIMER", "install", "uninstall"]


def install(
    prefix: str | None = None,
    *,
    config_path: str | None = None,
    password_spec: kopia_password.PasswordSpec | None = None,
    non_interactive: bool = False,
) -> int:
    root = root_prefix(prefix)
    # System dependencies gate the whole install and run before anything is
    # mutated: a missing dependency aborts cleanly with a copy-paste install
    # command for the detected OS release instead of leaving a half-installed
    # system behind.
    deps_code = installer_deps.ensure_system_deps(root, non_interactive=non_interactive)
    if deps_code != 0:
        return deps_code
    try:
        resolved_config = Path(config_path).expanduser() if config_path else default_config_path(root)
        validate_systemd_path(resolved_config, "config_path")
    except ValueError as exc:
        event("error", "invalid systemd unit path", error=str(exc))
        return 1
    cfg = Config.load(config_path=str(resolved_config), prefix=str(root), apply_env_overrides=False)
    try:
        with acquire_run_lock(cfg):
            config_existed = resolved_config.exists()
            if not config_existed:
                _apply_install_time_env(cfg)
            # A fresh install pointed at a backup tree that already holds a
            # shared config is *joining*: overlay that seed onto cfg before any
            # password/repo work so the whole install (password file path, repo
            # creation, units) runs against the shared settings. Install-time
            # env (BACKUP_PATH, NFS) still wins, and HOST_ID stays this host's.
            joining = False
            if not config_existed and cfg.get("BACKUP_PATH").strip():
                seed_values = config_sync.pull_shared_config_values(cfg)
                if seed_values is not None:
                    cfg.values.update(seed_values)
                    _apply_install_time_env(cfg)
                    joining = True
                    event("info", "seeded config from shared backup", path=str(config_sync.shared_config_path(cfg)))
            password_required = _install_backup_path_configured(
                cfg.get("BACKUP_PATH"),
                config_exists=config_existed,
            )
            if password_required and _host_id_preflight(cfg) != 0:
                return 1
            if password_required and _repo_preflight(cfg) != 0:
                return 1
            spec = password_spec or kopia_password.PasswordSpec()
            password_supplied = any(value is not None for value in (spec.literal, spec.file, spec.env_var))
            password_missing = not kopia_repo.password_file_path(cfg).exists()
            binaries_installed = False
            if password_required or password_supplied or password_missing:
                if _password_validation_needs_kopia(cfg):
                    binary_code = _install_pinned_binaries(root)
                    if binary_code != 0:
                        return binary_code
                    binaries_installed = True
                password_code = _install_password(cfg, spec)
                if password_code != 0:
                    return password_code
            if not binaries_installed:
                binary_code = _install_pinned_binaries(root)
                if binary_code != 0:
                    return binary_code
            install_code = _install_locked(root, resolved_config, cfg, joining=joining)
            if install_code != 0:
                return install_code
            if password_required:
                repo_code = _ensure_kopia_repo(cfg)
                if repo_code != 0:
                    return repo_code
            if cfg.get("BACKUP_PATH").strip():
                # First node: publish this host's fstab entry for the backup
                # mount so joining nodes are validated against it (the join
                # itself was already validated in _repo_preflight above).
                mount_consistency.record_local_mount(cfg)
            return 0
    except LockBusyError as exc:
        event("error", "another run in progress", lock_path=str(exc.path))
        return 1


def _install_pinned_binaries(root: Path) -> int:
    try:
        install_kopia(prefix=root)
    except BinaryInstallError as exc:
        event("error", "pinned binary install failed", error=str(exc))
        return 1
    return 0


def _password_validation_needs_kopia(cfg: Config) -> bool:
    if not cfg.get("BACKUP_PATH").strip():
        return False
    if kopia_repo.local_repo_exists(cfg):
        return True
    try:
        return any(peer.host_id != cfg.get("HOST_ID") for peer in kopia_repo.discover_peer_repos(cfg))
    except kopia_repo.PeerDiscoveryError:
        return False


def _ensure_kopia_repo(cfg: Config) -> int:
    if not cfg.get("BACKUP_PATH").strip():
        return 0
    if _repo_preflight(cfg, require_peer_access=True) != 0:
        return 1
    return kopia_repo.ensure_local_repo(cfg, apply_global_policy=True)


def _repo_preflight(cfg: Config, *, require_peer_access: bool = False) -> int:
    failures = preflight.repo_creation_failures(cfg)
    if require_peer_access:
        failures.extend(preflight.peer_repo_access_failures(cfg))
    for failure in failures:
        event("error", "kopia repo preflight failed", reason=failure)
    return 1 if failures else 0


def _host_id_preflight(cfg: Config) -> int:
    from . import preflight_host_id

    failure = preflight_host_id.validation_failure(cfg.get("HOST_ID"))
    if failure is None:
        return 0
    event("error", "kopia repo preflight failed", reason=failure)
    return 1


def _apply_install_time_env(cfg: Config) -> None:
    for env_key in INSTALL_TIME_ENV_KEYS:
        env_value = os.environ.get(env_key)
        if env_value is not None:
            cfg.values[env_key] = env_value


def _install_locked(root: Path, resolved_config: Path, cfg: Config, *, joining: bool = False) -> int:
    config_existed = resolved_config.exists()
    if not config_existed:
        _apply_install_time_env(cfg)
    try:
        configure_default_timeout(cfg.get("COMMAND_TIMEOUT_SECONDS"))
    except ValueError as exc:
        event("error", "invalid command timeout", error=str(exc))
        return 1
    package_src = Path(__file__).resolve().parent
    opt_dir = prefixed("/opt/libvirt-backup-system", root)
    package_dst = opt_dir / "libvirt_backup_system"
    bin_path = prefixed("/usr/local/bin/libvirt-backup-system", root)
    systemd_dir = prefixed("/etc/systemd/system", root)
    backup_path = cfg.get("BACKUP_PATH").strip()
    rendered = _render_units(cfg, root, bin_path, resolved_config) if backup_path else {}
    if backup_path and not rendered:
        return 1

    opt_dir.mkdir(parents=True, exist_ok=True)
    _install_package(package_src, package_dst)
    _write_wrapper(bin_path, root, opt_dir)
    install_fish_completion(root)

    resolved_config.parent.mkdir(parents=True, exist_ok=True)
    if not config_existed:
        _write_initial_config(resolved_config, cfg.render_env())
        # First node (no shared config found): publish ours so later joins can
        # pull it. seed_shared_config writes only when the seed is absent.
        if backup_path and not joining:
            config_sync.seed_shared_config(cfg, resolved_config)
    else:
        _log_dropped_install_time_env(resolved_config)

    _print_install_next_steps(resolved_config, bin_path)
    if not backup_path:
        return _install_without_backup_path(root, systemd_dir, resolved_config)

    systemd_dir.mkdir(parents=True, exist_ok=True)
    (systemd_dir / "libvirt-backup-system.service").write_text(rendered["service"], encoding="utf-8")
    (systemd_dir / "libvirt-backup-system-check.service").write_text(rendered["check"], encoding="utf-8")
    (systemd_dir / "libvirt-backup-system.timer").write_text(rendered["timer"], encoding="utf-8")
    (systemd_dir / MAINTENANCE_UNIT_NAME).write_text(rendered["maintenance_service"], encoding="utf-8")
    (systemd_dir / MAINTENANCE_TIMER_NAME).write_text(rendered["maintenance_timer"], encoding="utf-8")
    (systemd_dir / MAINTENANCE_FULL_UNIT_NAME).write_text(rendered["maintenance_full_service"], encoding="utf-8")
    (systemd_dir / MAINTENANCE_FULL_TIMER_NAME).write_text(rendered["maintenance_full_timer"], encoding="utf-8")
    (systemd_dir / VERIFY_UNIT_NAME).write_text(rendered["verify_service"], encoding="utf-8")
    (systemd_dir / VERIFY_TIMER_NAME).write_text(rendered["verify_timer"], encoding="utf-8")
    event("info", "installed", opt_dir=str(opt_dir), bin_path=str(bin_path), config_path=str(resolved_config))

    if not systemctl_available(root):
        event("info", "systemd reload skipped", root_prefix=str(root))
        return 0
    return 0 if run_systemctl(root, [["systemctl", "daemon-reload"]]) else 1


def uninstall(
    prefix: str | None = None,
    *,
    config_path: str | None = None,
    purge_config: bool = False,
    purge_state: bool = False,
    purge_logs: bool = False,
) -> int:
    root = root_prefix(prefix)
    cfg = Config.load(config_path=config_path, prefix=str(root), apply_env_overrides=False)
    try:
        with acquire_run_lock(cfg):
            return _uninstall_locked(
                root,
                cfg,
                purge_config=purge_config,
                purge_state=purge_state,
                purge_logs=purge_logs,
            )
    except LockBusyError as exc:
        event("error", "another run in progress", lock_path=str(exc.path))
        return 1
