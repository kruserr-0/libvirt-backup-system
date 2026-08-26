"""Typed-value validation of the env config, split out of ``preflight``.

Pure functions over ``Config`` values (required keys, booleans, integers,
floats, VM blacklist); the filesystem/binary/repo checks stay in
``preflight``. Split keeps both files under the project's 300-LOC ceiling.
"""

from __future__ import annotations

import math

from . import preflight_host_id
from .config import Config, float_value, int_value, split_words
from .config_data import CONFIG_KEYS
from .vms import is_safe_vm_uuid

BOOLEAN_KEYS = frozenset(("BACKUP_REQUIRE_FSTAB_CONSISTENCY", "BACKUP_REQUIRE_NFS_MOUNT", "REQUIRE_ROOT"))
INTEGER_KEYS = frozenset(
    "COMMAND_TIMEOUT_SECONDS KEEP_ANNUAL KEEP_DAILY KEEP_HOURLY KEEP_LATEST KEEP_MONTHLY KEEP_WEEKLY KOPIA_PARALLELISM SPACE_MARGIN_PERCENT".split()  # noqa: E501
)
FLOAT_KEYS = frozenset(("BACKUP_ESTIMATE_GB_PER_VM",))


def validate_required_present(config: Config) -> list[str]:
    optional_keys = {"KOPIA_REPO_PATH", "VM_BLACKLIST"}
    failures = [f"{k} must not be empty" for k in sorted(CONFIG_KEYS - optional_keys) if not config.get(k).strip()]
    host_id = config.get("HOST_ID")
    host_failure = preflight_host_id.validation_failure(host_id, allow_empty=True)
    if host_failure is not None:
        failures.append(host_failure)
    return failures


def validate_vm_blacklist(config: Config) -> list[str]:
    bad = [entry for entry in split_words(config.get("VM_BLACKLIST")) if not is_safe_vm_uuid(entry)]
    return [f"VM_BLACKLIST contains invalid VM UUID: {entry!r}" for entry in bad]


def validate_booleans(config: Config) -> list[str]:
    valid = {"1", "true", "yes", "on", "0", "false", "no", "off"}
    bad = [key for key in sorted(BOOLEAN_KEYS) if config.get(key).strip().lower() not in valid]
    return [f"{key} must be a boolean value" for key in bad]


def validate_integers(config: Config) -> list[str]:
    failures: list[str] = []
    for key in sorted(INTEGER_KEYS):
        try:
            value = int_value(config.values, key)
        except ValueError:
            failures.append(f"{key} must be an integer")
            continue
        if key == "COMMAND_TIMEOUT_SECONDS" and value <= 0:
            failures.append("COMMAND_TIMEOUT_SECONDS must be greater than 0")
        elif key != "COMMAND_TIMEOUT_SECONDS" and value < 0:
            failures.append(f"{key} must be greater than or equal to 0")
    return failures


def validate_floats(config: Config) -> list[str]:
    failures: list[str] = []
    for key in sorted(FLOAT_KEYS):
        try:
            value = float_value(config.values, key)
        except ValueError:
            failures.append(f"{key} must be a number")
            continue
        if not math.isfinite(value):
            failures.append(f"{key} must be a finite number")
            continue
        if value < 0:
            failures.append(f"{key} must be greater than or equal to 0")
    return failures
