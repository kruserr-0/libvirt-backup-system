"""Long-form help text for the ``change-password`` subcommand.

Split out of ``cli_help.py`` to keep each help module readable end-to-end.
Rendered verbatim by ``argparse.RawDescriptionHelpFormatter``.
"""

from __future__ import annotations

CHANGE_PASSWORD_HELP = "Rotate the shared kopia password on the local host."
CHANGE_PASSWORD_DESCRIPTION = """\
Rotate the kopia repo password the local host writes to. The same shared
password lives on every participating host: run this command (with the same
new value) on each host independently. Order does not matter; each host
rotates its own local repo and password file.

Pick one of:
  --new-kopia-password=VALUE         password on the command line (visible to ps/journald)
  --new-kopia-password-file=PATH     read from file; '-' means stdin
  --new-kopia-password-env=VAR       read from the named environment variable

Behavior:
  1. Validate the current password file decrypts the local repo.
  2. ``kopia repository change-password`` rewraps the master key.
  3. Atomically replace the password file with the new value.

Kopia's documented noninteractive rotation interface is
``repository change-password --new-password=...``. Even when this command
reads the new value from ``--new-kopia-password-file`` or
``--new-kopia-password-env``, the final call to Kopia must pass that value
in Kopia's argv; avoid running rotation on shared process-listing hosts.

If step 3 fails after step 2 succeeds, the repo decrypts only with the new
password but the file still holds the old one. The log line names both
values; restore the new value into the file manually and re-run
``doctor``."""
