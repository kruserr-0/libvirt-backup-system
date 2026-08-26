"""Install and remove the bash shell completion file for libvirt-backup-system.

The completion script itself lives in the package as
``libvirt_backup_system/data/libvirt-backup-system.bash``. Install copies it
to ``/usr/share/bash-completion/completions/`` (the standard vendor location
the bash-completion package auto-loads on first TAB, keyed by command name);
uninstall removes it. Both operations are best-effort and never abort the
surrounding install/uninstall when bash-completion is not present.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import prefixed
from .logging_json import event

# Standard on-demand completion dir. The file must be named exactly after the
# command (no extension) for bash-completion's lazy loader to find it.
BASH_COMPLETION_DIR = Path("/usr/share/bash-completion/completions")
BASH_COMPLETION_NAME = "libvirt-backup-system"
BASH_COMPLETION_SOURCE = "libvirt-backup-system.bash"


def _packaged_completion_path() -> Path:
    return Path(__file__).resolve().parent / "data" / BASH_COMPLETION_SOURCE


def bash_completion_target(root: Path) -> Path:
    return prefixed(BASH_COMPLETION_DIR / BASH_COMPLETION_NAME, root)


def install_bash_completion(root: Path) -> None:
    """Copy the bundled bash completion script under ``root``.

    Failures are logged at ``warning`` and swallowed: a read-only /usr/share
    or a hostile filesystem must not abort the rest of the install. The CLI
    works without completion; only TAB-driven discovery is degraded.
    """
    source = _packaged_completion_path()
    if not source.is_file():
        event("warning", "bash completion source missing in package", path=str(source))
        return
    target = bash_completion_target(root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError as exc:
        event("warning", "bash completion install skipped", path=str(target), error=str(exc))
        return
    event("info", "installed bash completion", path=str(target))


def remove_bash_completion(root: Path) -> bool:
    """Delete the previously installed bash completion script.

    Returns ``True`` when the file is gone (already absent or removed
    successfully) and ``False`` when a real OSError prevents removal; the
    uninstall exit code folds this in like the other cleanup helpers.
    """
    target = bash_completion_target(root)
    try:
        target.unlink()
    except FileNotFoundError:
        return True
    except OSError as exc:
        event("error", "failed to remove bash completion", path=str(target), error=str(exc))
        return False
    event("info", "removed bash completion", path=str(target))
    return True
