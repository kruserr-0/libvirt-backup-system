"""Route ad-hoc ``run``/``check`` invocations through the installed systemd unit.

Split out of ``systemd_units.py`` so unit naming/rendering/log tailing and the
dispatch decision logic each stay readable on their own.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys

from .config import bool_value, prefixed, root_prefix
from .logging_json import event
from .systemd_run_gate import manual_run_ready
from .systemd_units import CHECK_UNIT_NAME, RUN_UNIT_NAME, TIMER_UNIT_NAME, systemctl_available

DISPATCH_OPT_OUT_ENV = "LIBVIRT_BACKUP_NO_SYSTEMD_DISPATCH"


def unit_name_for(subcommand: str) -> str:
    if subcommand == "run":
        return RUN_UNIT_NAME
    if subcommand == "check":
        return CHECK_UNIT_NAME
    raise ValueError(f"no dispatch unit for subcommand: {subcommand}")


def dispatch_via_systemd(
    subcommand: str,
    *,
    prefix: str | None,
    config_path: str | None,
) -> int | None:
    """Run ``subcommand`` through the installed systemd unit.

    Returns the unit's exit code when dispatch is taken, or ``None`` when the
    caller should fall back to running the subcommand in-process. Falling back
    is the right thing whenever dispatch would change semantics:

    - ``INVOCATION_ID`` is set: we are already executing inside the unit and
      dispatching again would loop forever.
    - ``LIBVIRT_BACKUP_NO_SYSTEMD_DISPATCH`` is set: explicit operator opt-out
      for development or recovery.
    - ``--prefix`` is set: install rooted elsewhere; systemctl on this host
      manages a different (the real ``/``) install.
    - ``--config`` is set: the unit has a config path baked into ``ExecStart``;
      honoring a different path means staying in-process.
    - No systemctl available, or the unit file is not on disk yet.
    """
    if os.environ.get("INVOCATION_ID"):
        return None
    if bool_value(os.environ.get(DISPATCH_OPT_OUT_ENV, "")):
        return None
    if prefix is not None or config_path is not None:
        return None
    root = root_prefix(prefix)
    if not systemctl_available(root):
        return None
    unit = unit_name_for(subcommand)
    if not (prefixed("/etc/systemd/system", root) / unit).exists():
        if subcommand == "run":
            event(
                "error",
                "backup service is not running; run start before run",
                unit=unit,
                timer=TIMER_UNIT_NAME,
            )
            return 1
        return None
    if subcommand == "run":
        if not manual_run_ready(root, run_unit_name=RUN_UNIT_NAME, timer_unit_name=TIMER_UNIT_NAME):
            return 1
        # A manual ``run`` no longer blocks the operator's shell. Enqueue the
        # oneshot service with ``--no-block`` and return immediately: the
        # backup then runs under systemd (PID 1), surviving logout, terminal
        # close, and SSH disconnect. Progress is followed with ``log -f``; the
        # run lock in cli.py still serializes it against the scheduled timer.
        return _start_run_detached(unit)
    # ``check`` stays synchronous: it is a preflight whose exit code and output
    # the operator is waiting on, so block on the unit and tail this run.
    event("info", "dispatching to systemd unit", unit=unit, subcommand=subcommand)
    rc = _await_unit(unit)
    # ``systemctl show`` returns the most-recent invocation id even after the
    # unit has finished — filter the journal to just this run's output so the
    # operator sees exactly what the unit logged, with no surrounding noise.
    inv = subprocess.run(
        ["systemctl", "show", unit, "--property=InvocationID", "--value"],
        check=False,
        capture_output=True,
        text=True,
    )
    inv_id = inv.stdout.strip()
    if inv_id:
        subprocess.run(
            ["journalctl", f"_SYSTEMD_INVOCATION_ID={inv_id}", "--output=cat", "--no-pager"],
            check=False,
            stdout=sys.stderr,
        )
    if rc == 0:
        event("info", "check passed", unit=unit)
    return rc


def _start_run_detached(unit: str) -> int:
    """Start the backup unit in the background and return immediately.

    ``systemctl start --no-block`` enqueues the start job and returns as soon
    as systemd accepts it, instead of waiting for the oneshot service to
    finish. The operator's shell is freed at once and the backup keeps running
    under systemd. Follow it with ``log -f``.
    """
    result = subprocess.run(
        ["systemctl", "start", "--no-block", unit],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        event(
            "error",
            "failed to start backup service",
            unit=unit,
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )
        return result.returncode
    event(
        "info",
        "backup started in background",
        unit=unit,
        follow="libvirt-backup-system log -f",
    )
    return 0


def _await_unit(unit: str) -> int:
    """Run ``systemctl start --wait`` and forward Ctrl-C to the unit.

    A bare ``systemctl start --wait`` propagates SIGINT to its own process but
    not to the unit it is waiting on — the operator's Ctrl-C returns 130 to
    the shell while the unit and the held run lock keep running. Install a
    handler that issues ``systemctl stop --no-block`` so the unit is asked to
    stop in the background; we then keep waiting until systemctl returns so
    the exit code reflects the unit's real outcome (stopped, killed, etc.)
    instead of the partial 130.
    """
    previous = signal.getsignal(signal.SIGINT)

    def _forward(_signum: int, _frame: object) -> None:
        with contextlib.suppress(OSError):
            subprocess.run(["systemctl", "stop", "--no-block", unit], check=False)
        event("info", "forwarded SIGINT to systemd unit via stop --no-block", unit=unit)

    signal.signal(signal.SIGINT, _forward)
    try:
        return subprocess.run(["systemctl", "start", "--wait", unit], check=False).returncode
    finally:
        signal.signal(signal.SIGINT, previous)
