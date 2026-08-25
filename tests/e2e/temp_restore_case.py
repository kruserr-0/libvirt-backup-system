"""Real-KVM e2e for temp-restore clones and the overwrite-restore guards.

Runs against qemu:///session exactly like ``real_kvm_case`` (and reuses its
helpers). The scenario keeps one "production" VM running throughout and:

* temp-restores a backup into a clone that runs BESIDE the untouched prod VM
  (fresh UUID, fresh MAC, disks under the temp-restore state dir),
* drives the clone lifecycle end-to-end (list / duplicate refusal / stop /
  remove) and verifies the prod disk bytes never change,
* exercises the overwrite-restore guards: refusal without ``-y`` on a
  non-TTY stdin, the default pre-restore safety backup (meta snapshot count
  grows), and ``--no-pre-backup`` skipping it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.e2e.real_kvm_case import (
    _install,
    _KopiaCtx,
    _pick_restore_point,
    _run,
    _session_domain_uuids,
    _teardown_domains,
    _virsh,
    _write_config,
    real_kvm_skip_reason,
)

PROD_MAC = "52:54:00:aa:bb:01"


def _domain_xml_with_nic(name: str, disk_path: Path) -> str:
    return (
        f"<domain type='kvm'><name>{name}</name><uuid>{uuid.uuid4()}</uuid>"
        f"<memory unit='MiB'>32</memory><currentMemory unit='MiB'>32</currentMemory>"
        f"<vcpu placement='static'>1</vcpu>"
        f"<os><type arch='x86_64' machine='pc'>hvm</type></os>"
        f"<features><acpi/></features>"
        f"<on_poweroff>destroy</on_poweroff><on_reboot>destroy</on_reboot><on_crash>destroy</on_crash>"
        f"<devices><emulator>/usr/bin/qemu-system-x86_64</emulator>"
        f"<disk type='file' device='disk'>"
        f"<driver name='qemu' type='qcow2'/><source file='{disk_path}'/>"
        f"<target dev='vda' bus='virtio'/></disk>"
        f"<interface type='user'><mac address='{PROD_MAC}'/><model type='virtio'/></interface>"
        f"</devices></domain>\n"
    )


def _define_running_domain(work: Path, name: str) -> Path:
    disk = work / f"{name}.qcow2"
    _run(["qemu-img", "create", "-f", "qcow2", str(disk), "16M"])
    xml_path = work / f"{name}.xml"
    xml_path.write_text(_domain_xml_with_nic(name, disk), encoding="utf-8")
    _virsh(["define", str(xml_path)])
    _virsh(["start", name])
    return disk


def _run_no_tty(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run with stdin from /dev/null so the overwrite guard sees a non-TTY."""
    proc = subprocess.run(
        args, text=True, capture_output=True, stdin=subprocess.DEVNULL, env=os.environ.copy(), check=False
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(args)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _domain_macs(name: str) -> set[str]:
    root = ET.fromstring(_virsh(["dumpxml", name]).stdout)
    return {mac.get("address", "") for mac in root.findall(".//devices/interface/mac")}


def _domain_disk_sources(name: str) -> set[str]:
    root = ET.fromstring(_virsh(["dumpxml", name]).stdout)
    return {source.get("file", "") for source in root.findall(".//devices/disk/source")}


def _temp_restore_rows(bin_path: Path) -> list[dict[str, str]]:
    return json.loads(_run([str(bin_path), "temp-restore", "list", "--json"]).stdout or "[]")


def _assert_clone_runs_beside_prod(prefix: Path, prod_name: str, prod_uuid: str, temp_name: str) -> None:
    assert _virsh(["domstate", prod_name]).stdout.strip() == "running"
    assert _virsh(["domstate", temp_name]).stdout.strip() == "running"
    temp_uuid = _virsh(["domuuid", temp_name]).stdout.strip()
    assert temp_uuid and temp_uuid != prod_uuid, f"clone must not reuse the prod UUID: {temp_uuid}"
    assert _domain_macs(temp_name).isdisjoint(_domain_macs(prod_name)), "clone must not reuse the prod MAC"
    temp_root = prefix / "var/lib/libvirt-backup-system/temp-restore"
    temp_sources = _domain_disk_sources(temp_name)
    assert all(src.startswith(str(temp_root)) for src in temp_sources), temp_sources
    for src in temp_sources:
        assert Path(src).is_file(), f"clone disk missing on disk: {src}"


def _clone_scenario(bin_path: Path, prefix: Path, prod_name: str, prod_uuid: str, prod_disk: Path) -> None:
    prod_hash = _sha256(prod_disk)
    timestamp = _pick_restore_point(bin_path, prod_uuid)
    temp_name = f"{prod_name}-temp-{timestamp}"

    _run([str(bin_path), "temp-restore", "restore", prod_uuid, timestamp])
    _assert_clone_runs_beside_prod(prefix, prod_name, prod_uuid, temp_name)
    assert _sha256(prod_disk) == prod_hash, "temp restore must not touch the prod disk"

    rows = _temp_restore_rows(bin_path)
    assert [row["temp_name"] for row in rows] == [temp_name], rows
    assert rows[0]["state"] == "running"
    assert rows[0]["source_vm_uuid"] == prod_uuid

    duplicate = _run([str(bin_path), "temp-restore", "restore", prod_uuid, timestamp], check=False)
    assert duplicate.returncode != 0, "restoring the same point twice must be refused"
    assert _virsh(["domstate", temp_name]).stdout.strip() == "running"

    _run([str(bin_path), "temp-restore", "stop", temp_name])
    assert _virsh(["domstate", temp_name]).stdout.strip() == "shut off"
    assert _temp_restore_rows(bin_path)[0]["state"] == "shut off"

    staging = prefix / "var/lib/libvirt-backup-system/temp-restore" / f"{prod_uuid}-{timestamp}"
    assert staging.is_dir()
    _run([str(bin_path), "temp-restore", "remove", temp_name])
    assert _virsh(["domuuid", temp_name], check=False).returncode != 0, "clone must be undefined after remove"
    assert not staging.exists(), "remove must delete the clone staging dir"
    assert _temp_restore_rows(bin_path) == []
    assert _virsh(["domstate", prod_name]).stdout.strip() == "running"
    assert _sha256(prod_disk) == prod_hash, "clone lifecycle must not touch the prod disk"


def _overwrite_guard_scenario(bin_path: Path, ctx: _KopiaCtx, prod_name: str, prod_uuid: str) -> None:
    timestamp = _pick_restore_point(bin_path, prod_uuid)

    refused = _run_no_tty([str(bin_path), "restore", prod_uuid, timestamp], check=False)
    assert refused.returncode != 0, "overwrite restore must refuse without -y on a non-TTY stdin"
    assert "refusing overwrite restore without confirmation" in refused.stdout + refused.stderr
    assert _virsh(["domstate", prod_name]).stdout.strip() == "running"

    meta_before = ctx.snapshot_count({"vm-uuid": prod_uuid, "kind": "meta"})
    _run_no_tty([str(bin_path), "restore", "-y", prod_uuid, timestamp])
    assert _virsh(["domstate", prod_name]).stdout.strip() == "running"
    meta_after = ctx.snapshot_count({"vm-uuid": prod_uuid, "kind": "meta"})
    assert (
        meta_after == meta_before + 1
    ), f"default overwrite restore must take a pre-restore safety backup ({meta_before} -> {meta_after})"

    _run_no_tty([str(bin_path), "restore", "-y", "--no-pre-backup", prod_uuid, timestamp])
    assert _virsh(["domstate", prod_name]).stdout.strip() == "running"
    meta_final = ctx.snapshot_count({"vm-uuid": prod_uuid, "kind": "meta"})
    assert meta_final == meta_after, "--no-pre-backup must skip the safety backup"


def _run_scenario(work: Path, prod_name: str) -> None:
    backup_path = work / "backup"
    backup_path.mkdir()
    prefix = work / "root"
    config_path = prefix / "etc/libvirt-backup-system/libvirt-backup.env"
    prod_disk = work / f"{prod_name}.qcow2"
    prod_uuid = _virsh(["domuuid", prod_name]).stdout.strip()
    pre_existing = [uid for uid in _session_domain_uuids() if uid != prod_uuid]
    _write_config(config_path, backup_path=backup_path, blacklist=pre_existing)
    bin_path, ctx = _install(prefix, backup_path)

    _run([str(bin_path), "check"])
    _run([str(bin_path), "run"])
    assert ctx.snapshot_count({"vm-uuid": prod_uuid, "kind": "meta"}) >= 1

    _clone_scenario(bin_path, prefix, prod_name, prod_uuid, prod_disk)
    _overwrite_guard_scenario(bin_path, ctx, prod_name, prod_uuid)


def main() -> int:
    reason = real_kvm_skip_reason()
    if reason:
        print(f"temp-restore e2e cannot run: {reason}", file=sys.stderr)
        return 1
    tag = uuid.uuid4().hex[:8]
    prod_name = f"lbs-e2e-{tag}-prod"
    work = Path(tempfile.mkdtemp(prefix="lbs-e2e-temp-restore-"))
    print(f"temp-restore e2e: work={work} prod={prod_name}", flush=True)
    try:
        _define_running_domain(work, prod_name)
        _run_scenario(work, prod_name)
        print("temp-restore e2e: PASS", flush=True)
        return 0
    finally:
        # A failed run can leave the clone behind; sweep every domain that
        # carries this run's prod-name prefix (``virsh list --name`` prints
        # one bare name per line).
        leftover_clones = [
            line.strip()
            for line in _virsh(["list", "--all", "--name"], check=False).stdout.splitlines()
            if line.strip().startswith(f"{prod_name}-temp-")
        ]
        _teardown_domains((*leftover_clones, prod_name))
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
