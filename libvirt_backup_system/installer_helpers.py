from __future__ import annotations

import os
import shlex
import shutil
import stat
import sys
from pathlib import Path

from .config import Config
from .logging_json import event
from .systemd_units import (
    KOPIA_FULL_MAINTENANCE_INTERVAL,
    KOPIA_TIMER_ON_ACTIVE_SEC,
    KOPIA_UNIT_DESCRIPTIONS,
    MAINTENANCE_FULL_TIMER_NAME,
    MAINTENANCE_FULL_UNIT_NAME,
    MAINTENANCE_TIMER_NAME,
    MAINTENANCE_UNIT_NAME,
    VERIFY_TIMER_NAME,
    VERIFY_UNIT_NAME,
    render_unit_interval_timer,
    render_unit_kopia_service,
    render_unit_service,
    render_unit_timer,
    run_systemctl,
)

# First-install env keys that must be persisted into libvirt-backup.env so the
# same values are used by the rendered systemd units and immediate repo setup.
INSTALL_TIME_ENV_KEYS = ("BACKUP_PATH", "BACKUP_REQUIRE_NFS_MOUNT")


def install_time_env_keys_present() -> list[str]:
    return [key for key in INSTALL_TIME_ENV_KEYS if os.environ.get(key) is not None]


def install_backup_path_configured(config_backup_path: str, *, config_exists: bool) -> bool:
    if config_exists:
        return bool(config_backup_path.strip())
    return bool(os.environ.get("BACKUP_PATH", config_backup_path).strip())


def log_dropped_install_time_env(path: Path) -> None:
    dropped = install_time_env_keys_present()
    if dropped:
        event(
            "info",
            "existing config kept; install-time environment ignored",
            path=str(path),
            keys=",".join(dropped),
        )


def install_package(package_src: Path, package_dst: Path) -> None:
    # ``sudo libvirt-backup-system install`` imports this module from
    # /opt/libvirt-backup-system, which is package_dst — an rmtree+copytree
    # there would delete the live source mid-execute. Detect the self-source
    # case and skip the package copy; refresh code from a source checkout via
    # ``sudo python3 -m libvirt_backup_system install``.
    resolved_src = package_src.resolve()
    resolved_dst = package_dst.resolve() if package_dst.exists() else package_dst
    if resolved_src == resolved_dst or resolved_dst in resolved_src.parents:
        event(
            "info",
            "install reusing in-place package (source equals destination); "
            "re-run from a source checkout to refresh code",
            package_src=str(resolved_src),
            package_dst=str(resolved_dst),
        )
        return
    if package_dst.exists():
        shutil.rmtree(package_dst)
    shutil.copytree(package_src, package_dst)


def write_wrapper(bin_path: Path, root: Path, opt_dir: Path) -> None:
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    python = sys.executable if root != Path("/") else (shutil.which("python3") or "/usr/bin/python3")
    quoted_root = shlex.quote(str(root))
    quoted_opt_dir = shlex.quote(str(opt_dir))
    quoted_python = shlex.quote(python)
    wrapper = (
        "#!/bin/sh\n"
        f"export LIBVIRT_BACKUP_ROOT_PREFIX={quoted_root}\n"
        f"export PYTHONPATH={quoted_opt_dir}${{PYTHONPATH:+:$PYTHONPATH}}\n"
        f'exec {quoted_python} -m libvirt_backup_system "$@"\n'
    )
    bin_path.write_text(wrapper, encoding="utf-8")
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_without_backup_path(root: Path, systemd_dir: Path, resolved_config: Path) -> int:
    ok = run_systemctl(
        root,
        [
            ["systemctl", "disable", "--now", "libvirt-backup-system.timer"],
            ["systemctl", "stop", "libvirt-backup-system.service"],
            ["systemctl", "disable", "--now", MAINTENANCE_TIMER_NAME],
            ["systemctl", "stop", MAINTENANCE_UNIT_NAME],
            ["systemctl", "disable", "--now", MAINTENANCE_FULL_TIMER_NAME],
            ["systemctl", "stop", MAINTENANCE_FULL_UNIT_NAME],
            ["systemctl", "disable", "--now", VERIFY_TIMER_NAME],
            ["systemctl", "stop", VERIFY_UNIT_NAME],
        ],
    )
    for path in [
        systemd_dir / "libvirt-backup-system.service",
        systemd_dir / "libvirt-backup-system-check.service",
        systemd_dir / "libvirt-backup-system.timer",
        systemd_dir / MAINTENANCE_UNIT_NAME,
        systemd_dir / MAINTENANCE_TIMER_NAME,
        systemd_dir / MAINTENANCE_FULL_UNIT_NAME,
        systemd_dir / MAINTENANCE_FULL_TIMER_NAME,
        systemd_dir / VERIFY_UNIT_NAME,
        systemd_dir / VERIFY_TIMER_NAME,
    ]:
        try:
            path.unlink()
            event("info", "removed stale systemd unit", path=str(path))
        except FileNotFoundError:
            pass
        except (PermissionError, OSError) as exc:
            event("error", "failed to remove stale systemd unit", path=str(path), error=str(exc))
            ok = False
    event(
        "warning",
        "systemd unit installation skipped because BACKUP_PATH is not configured",
        config_path=str(resolved_config),
    )
    ok = run_systemctl(root, [["systemctl", "daemon-reload"]]) and ok
    return 0 if ok else 1


def write_initial_config(path: Path, content: str) -> None:
    # O_CREAT|O_EXCL|0o600 in one open: write_text + chmod 0o600 would leave
    # a window where the env (libvirt URI, possibly secrets) is world-readable.
    # O_EXCL also closes the exists()-vs-open TOCTOU window so a parallel
    # writer's file is left intact instead of truncated.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        log_dropped_install_time_env(path)
        return
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)


def render_units(cfg: Config, root: Path, bin_path: Path, resolved_config: Path) -> dict[str, str]:
    backup_path = cfg.get("BACKUP_PATH").strip()
    try:
        service_text = render_unit_service(backup_path, bin_path, resolved_config, subcommand="run")
        check_service_text = render_unit_service(backup_path, bin_path, resolved_config, subcommand="check")
        maintenance_service = render_unit_kopia_service(
            bin_path,
            resolved_config,
            kind="maintenance",
            backup_path=backup_path,
        )
        maintenance_full_service = render_unit_kopia_service(
            bin_path,
            resolved_config,
            kind="maintenance-full",
            backup_path=backup_path,
        )
        verify_service = render_unit_kopia_service(bin_path, resolved_config, kind="verify", backup_path=backup_path)
    except ValueError as exc:
        event("error", "invalid systemd unit path", error=str(exc))
        return {}
    timer_text = render_unit_timer(root, cfg.get("SYSTEMD_ON_CALENDAR"))
    maintenance_timer = render_unit_interval_timer(
        description=KOPIA_UNIT_DESCRIPTIONS["maintenance"],
        interval=cfg.get("KOPIA_MAINTENANCE_INTERVAL"),
        on_active_sec=KOPIA_TIMER_ON_ACTIVE_SEC["maintenance"],
    )
    maintenance_full_timer = render_unit_interval_timer(
        description=KOPIA_UNIT_DESCRIPTIONS["maintenance-full"],
        interval=KOPIA_FULL_MAINTENANCE_INTERVAL,
        on_active_sec=KOPIA_TIMER_ON_ACTIVE_SEC["maintenance-full"],
    )
    verify_timer = render_unit_interval_timer(
        description=KOPIA_UNIT_DESCRIPTIONS["verify"],
        interval=cfg.get("KOPIA_VERIFY_INTERVAL"),
        on_active_sec=KOPIA_TIMER_ON_ACTIVE_SEC["verify"],
    )
    if timer_text is None or maintenance_timer is None or maintenance_full_timer is None or verify_timer is None:
        return {}
    return {
        "service": service_text,
        "check": check_service_text,
        "timer": timer_text,
        "maintenance_service": maintenance_service,
        "maintenance_timer": maintenance_timer,
        "maintenance_full_service": maintenance_full_service,
        "maintenance_full_timer": maintenance_full_timer,
        "verify_service": verify_service,
        "verify_timer": verify_timer,
    }


def print_install_next_steps(config_path: Path, bin_path: Path) -> None:
    lines = [
        "",
        "Next steps:",
        f"  sudoedit {config_path}",
        "",
        "Set the required backup path, then run start so the systemd unit matches it:",
        "  BACKUP_PATH=/mnt/qnap-backups",
        "",
        "BACKUP_PATH must be a mounted shared filesystem (NFS) by default. For an",
        "intentionally local backup directory, uncomment and set:",
        "  BACKUP_REQUIRE_NFS_MOUNT=false",
        "",
        "Retention is governed by Kopia's policy keys. Defaults keep latest snapshots plus one year.",
        "Tune KEEP_DAILY (and the other KEEP_* keys) in the env file; expired snapshots are pruned by",
        "the kopia maintenance timer in the background.",
        "",
        "Then validate, initialize the repo from the env file, and run the first backup:",
        f"  sudo {bin_path} check",
        f"  sudo {bin_path} start",
        f"  sudo {bin_path} doctor",
        f"  sudo {bin_path} run",
        "",
    ]
    print("\n".join(lines), flush=True)
