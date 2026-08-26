"""Argument-parser wiring for the ``install`` subcommand.

Lives in its own module (like ``cli_parser_temp_restore``) purely to keep
``cli_parser`` under the project's 300-LOC ceiling; ``build_parser`` calls
``add_install_parser`` with its shared subparser/password-flag helpers.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from . import cli_help

AddSubparser = Callable[..., argparse.ArgumentParser]
AddPasswordFlags = Callable[..., None]


def add_install_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],  # pyright: ignore[reportPrivateUsage]
    *,
    add_subparser: AddSubparser,
    add_password_flags: AddPasswordFlags,
) -> None:
    install_parser = add_subparser(
        sub, "install", help_text=cli_help.INSTALL_HELP, description=cli_help.INSTALL_DESCRIPTION
    )
    add_password_flags(install_parser, prefix="")
    install_parser.add_argument(
        "--acknowledge-password-loss",
        action="store_true",
        help=(
            "Required on first install: confirms this password has been stored outside "
            "libvirt-backup-system and that losing it makes all backups unrecoverable."
        ),
    )
    install_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=(
            "Assume yes to the system-dependency install confirmation, so missing apt "
            "packages are installed without asking. Required together with "
            "--non-interactive for the automatic install."
        ),
    )
    install_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Never prompt (for automation tooling). With -y, missing system dependencies "
            "are still apt-installed when root or passwordless sudo is available (skipped "
            "otherwise); without -y they fail the install with a copy-paste apt command."
        ),
    )
    install_parser.add_argument(
        "--reinstall-deps",
        action="store_true",
        help=(
            "Force apt-get install --reinstall of ALL system dependency packages and "
            "re-extract the pinned kopia binary, even when they look present. Use to "
            "repair dependencies that are broken for whatever reason."
        ),
    )
