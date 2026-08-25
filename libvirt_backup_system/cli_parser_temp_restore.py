"""Argument-parser wiring for the ``temp-restore`` subcommand tree.

Lives in its own module (instead of ``cli_parser.py``) purely to keep each
parser module small; ``build_parser`` calls ``add_temp_restore_parser``.
"""

from __future__ import annotations

import argparse

from . import cli_help_restore


def _add_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
    name: str,
    *,
    help_text: str,
    description: str | None = None,
) -> argparse.ArgumentParser:
    return sub.add_parser(
        name,
        help=help_text,
        description=description or help_text,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def add_temp_restore_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
) -> None:
    temp_parser = _add_parser(
        sub,
        "temp-restore",
        help_text=cli_help_restore.TEMP_RESTORE_HELP,
        description=cli_help_restore.TEMP_RESTORE_DESCRIPTION,
    )
    temp_sub = temp_parser.add_subparsers(
        dest="temp_command",
        required=True,
        title="temp-restore subcommands",
        metavar="<subcommand>",
    )

    restore_parser = _add_parser(
        temp_sub,
        "restore",
        help_text=cli_help_restore.TEMP_RESTORE_RESTORE_HELP,
        description=cli_help_restore.TEMP_RESTORE_RESTORE_DESCRIPTION,
    )
    restore_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Stream full restore output instead of only summary success/error events.",
    )
    restore_parser.add_argument(
        "--host-id",
        metavar="HOST_ID",
        help="Disambiguate duplicate VM UUID/timestamp matches by source-host-id.",
    )
    restore_parser.add_argument(
        "--run-id",
        metavar="RUN_ID",
        help="Disambiguate duplicate VM UUID/timestamp matches by run-id.",
    )
    restore_parser.add_argument(
        "vm_uuid",
        metavar="VM_UUID",
        help="VM libvirt UUID copied verbatim from the vm-uuid column of list-restore-points output.",
    )
    restore_parser.add_argument(
        "timestamp",
        metavar="TIMESTAMP",
        help=(
            "Per-run timestamp (YYYYMMDDTHHMMSS) copied verbatim from the timestamp column of "
            "list-restore-points output. Exact match against the meta snapshot's timestamp tag."
        ),
    )

    list_parser = _add_parser(temp_sub, "list", help_text=cli_help_restore.TEMP_RESTORE_LIST_HELP)
    list_parser.add_argument("--json", action="store_true", help="Emit JSON array instead of table rows.")

    stop_parser = _add_parser(temp_sub, "stop", help_text=cli_help_restore.TEMP_RESTORE_STOP_HELP)
    stop_parser.add_argument(
        "name",
        metavar="TEMP_VM_NAME",
        help="Clone VM name from the temp-name column of temp-restore list output.",
    )

    remove_parser = _add_parser(
        temp_sub,
        "remove",
        help_text=cli_help_restore.TEMP_RESTORE_REMOVE_HELP,
        description=cli_help_restore.TEMP_RESTORE_REMOVE_DESCRIPTION,
    )
    remove_parser.add_argument(
        "name",
        metavar="TEMP_VM_NAME",
        help="Clone VM name from the temp-name column of temp-restore list output.",
    )
