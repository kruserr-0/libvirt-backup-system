"""CLI dispatch for the ``restore`` and ``temp-restore`` subcommands.

Kept out of ``cli.py`` so the main dispatcher stays a thin routing table.
Both restore flavors validate config first and hold the same run-lock as
``run`` while they read the kopia repos; the temp-restore management
subcommands (list/stop/remove) never touch the repos and skip the lock.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from .config import Config
from .lock import LockBusyError, acquire_run_lock
from .logging_json import event
from .preflight import validate_config
from .restore import restore
from .temp_restore import restore_temp
from .temp_restore_manage import list_temp_restores, remove_temp_restore, stop_temp_restore


def _locked(config: Config, action: Callable[[], int]) -> int:
    try:
        with acquire_run_lock(config):
            return action()
    except LockBusyError as exc:
        event("error", "another run in progress", lock_path=str(exc.path))
        return 1


def restore_command(config: Config, args: argparse.Namespace) -> int:
    config_code = validate_config(config)
    if config_code != 0:
        return config_code
    return _locked(
        config,
        lambda: restore(
            config,
            args.vm_uuid,
            args.timestamp,
            host_id=args.host_id,
            run_id=args.run_id,
            verbose=args.verbose,
            assume_yes=args.yes,
            pre_backup=not args.no_pre_backup,
        ),
    )


def temp_restore_command(config: Config, args: argparse.Namespace) -> int:
    config_code = validate_config(config)
    if config_code != 0:
        return config_code
    if args.temp_command == "restore":
        return _locked(
            config,
            lambda: restore_temp(
                config,
                args.vm_uuid,
                args.timestamp,
                host_id=args.host_id,
                run_id=args.run_id,
                verbose=args.verbose,
            ),
        )
    if args.temp_command == "list":
        return list_temp_restores(config, json_output=args.json)
    if args.temp_command == "stop":
        return stop_temp_restore(config, args.name)
    return remove_temp_restore(config, args.name)
