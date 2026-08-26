"""Execute the bash completion script in a real bash and assert COMPREPLY.

Mirrors ``test_fish_completion_dynamic.py``: the script is sourced in a
subprocess with COMP_WORDS/COMP_CWORD staged, and the resulting COMPREPLY is
printed one candidate per line. The dynamic restore-point suggestions read a
pre-seeded cache file under XDG_CACHE_HOME, so no real ``sudo`` or installed
CLI is needed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from libvirt_backup_system.bash_completion import _packaged_completion_path

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

CACHE_HEADER = "source-host-id vm-uuid timestamp run-id consistency vm-name"
UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _seed_cache(tmp_path: Path) -> Path:
    cache_root = tmp_path / "xdg-cache"
    cache = cache_root / "libvirt-backup-system/restore-points.tsv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        f"{CACHE_HEADER}\n"
        f"host-a {UUID_A} 20260101T010101 run-1 filesystem web\n"
        f"host-a {UUID_A} 20260202T020202 run-2 filesystem web\n"
        f"host-b {UUID_B} 20260303T030303 run-3 crash db\n",
        encoding="utf-8",
    )
    return cache_root


def _complete(tmp_path: Path, words: list[str], *, cword: int | None = None) -> list[str]:
    """Source the completion script and return COMPREPLY for ``words``."""
    assert BASH is not None
    cache_root = _seed_cache(tmp_path)
    index = cword if cword is not None else len(words) - 1
    staged = " ".join(f"'{word}'" for word in words)
    script = (
        f"source '{_packaged_completion_path()}'\n"
        f"COMP_WORDS=({staged})\n"
        f"COMP_CWORD={index}\n"
        "_libvirt_backup_system\n"
        'for candidate in "${COMPREPLY[@]}"; do printf \'%s\\n\' "$candidate"; done\n'
    )
    completed = subprocess.run(
        [BASH, "--noprofile", "--norc", "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={"XDG_CACHE_HOME": str(cache_root), "PATH": "/usr/bin:/bin"},
    )
    return [line for line in completed.stdout.splitlines() if line]


def test_completes_subcommands(tmp_path: Path) -> None:
    candidates = _complete(tmp_path, ["libvirt-backup-system", ""])
    assert "install" in candidates
    assert "restore" in candidates
    assert "temp-restore" in candidates


def test_completes_subcommand_prefix(tmp_path: Path) -> None:
    candidates = _complete(tmp_path, ["libvirt-backup-system", "che"])
    assert candidates == ["check"]


def test_completes_global_flags(tmp_path: Path) -> None:
    candidates = _complete(tmp_path, ["libvirt-backup-system", "--"])
    assert "--config" in candidates
    assert "--prefix" in candidates


def test_completes_install_flags(tmp_path: Path) -> None:
    candidates = _complete(tmp_path, ["libvirt-backup-system", "install", "--"])
    assert "--non-interactive" in candidates
    assert "--reinstall-deps" in candidates
    assert "--kopia-password-env" in candidates


def test_completes_restore_uuid_then_timestamp(tmp_path: Path) -> None:
    uuids = _complete(tmp_path, ["libvirt-backup-system", "restore", ""])
    assert sorted(uuids) == [UUID_A, UUID_B]
    stamps = _complete(tmp_path, ["libvirt-backup-system", "restore", UUID_A, ""])
    # Most recent first so "restore the latest" is the top suggestion.
    assert stamps == ["20260202T020202", "20260101T010101"]


def test_restore_flags_do_not_shift_positionals(tmp_path: Path) -> None:
    stamps = _complete(
        tmp_path,
        ["libvirt-backup-system", "restore", "--host-id", "host-a", "-v", UUID_A, ""],
    )
    assert stamps == ["20260202T020202", "20260101T010101"]


def test_completes_temp_restore_subcommands_and_uuids(tmp_path: Path) -> None:
    subcommands = _complete(tmp_path, ["libvirt-backup-system", "temp-restore", ""])
    assert sorted(subcommands) == ["list", "remove", "restore", "stop"]
    uuids = _complete(tmp_path, ["libvirt-backup-system", "temp-restore", "restore", ""])
    assert sorted(uuids) == [UUID_A, UUID_B]


def test_completes_du_hosts_then_vm_uuids(tmp_path: Path) -> None:
    first = _complete(tmp_path, ["libvirt-backup-system", "du", ""])
    assert set(first) == {"host-a", "host-b", UUID_A, UUID_B}
    second = _complete(tmp_path, ["libvirt-backup-system", "du", "host-a", ""])
    assert second == [UUID_A]


def test_completes_log_components(tmp_path: Path) -> None:
    candidates = _complete(tmp_path, ["libvirt-backup-system", "log", ""])
    assert "maintenance-full" in candidates
    assert "all" in candidates
