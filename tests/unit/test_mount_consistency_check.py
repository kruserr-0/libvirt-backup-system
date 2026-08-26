"""Preflight-facing tests for ``mount_consistency.consistency_failures``."""

from __future__ import annotations

from pathlib import Path

import pytest

from libvirt_backup_system import config_sync, mount_consistency
from libvirt_backup_system.config import Config
from tests.unit.test_mount_consistency import NFS_SOURCE, make_config, mount_line, write_fstab, write_proc_mounts


def _record_current_fstab(cfg: Config) -> None:
    mount_consistency.record_local_mount(cfg, overwrite=True)


def test_disabled_flag_skips_everything(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.values["BACKUP_REQUIRE_FSTAB_CONSISTENCY"] = "false"
    write_fstab(cfg, mount_line(cfg))
    write_proc_mounts(cfg, mount_line(cfg, source="10.6.6.6:/other"))
    assert mount_consistency.consistency_failures(cfg) == []


def test_empty_backup_path_skips(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, backup_path="")
    assert mount_consistency.consistency_failures(cfg) == []


def test_clean_single_node_before_any_recording(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    write_proc_mounts(cfg, mount_line(cfg))
    assert mount_consistency.consistency_failures(cfg) == []


def test_live_mount_differs_from_fstab(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    write_proc_mounts(cfg, mount_line(cfg, source="10.0.0.9:/export/backups"))
    failures = mount_consistency.consistency_failures(cfg)
    assert len(failures) == 1
    assert "mounted from '10.0.0.9:/export/backups' but fstab says" in failures[0]
    assert "remount" in failures[0]


def test_live_vs_fstab_only_compared_for_remote_sources(tmp_path: Path) -> None:
    # Local block devices legitimately differ between fstab (UUID=...) and
    # /proc/self/mounts (/dev/sda1); only server:/export sources compare.
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg, source="UUID=abcd", fstype="ext4"))
    write_proc_mounts(cfg, mount_line(cfg, source="/dev/sda1", fstype="ext4"))
    assert mount_consistency.consistency_failures(cfg) == []


def test_matching_node_passes_against_recorded_entry(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    write_proc_mounts(cfg, mount_line(cfg))
    _record_current_fstab(cfg)
    assert mount_consistency.consistency_failures(cfg) == []


def test_option_order_is_ignored(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    _record_current_fstab(cfg)
    write_fstab(cfg, mount_line(cfg, options="timeo=600,hard,rw"))
    assert mount_consistency.consistency_failures(cfg) == []


def test_missing_fstab_entry_fails_when_peers_recorded_one(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    _record_current_fstab(cfg)
    write_fstab(cfg, "# nothing here\n")
    failures = mount_consistency.consistency_failures(cfg)
    assert len(failures) == 1
    assert "no /etc/fstab entry found" in failures[0]
    assert NFS_SOURCE in failures[0]
    assert "recorded by host-a" in failures[0]


def test_changed_server_address_fails_with_both_entries(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    _record_current_fstab(cfg)
    write_fstab(cfg, mount_line(cfg, source="10.9.9.9:/export/backups"))
    write_proc_mounts(cfg, mount_line(cfg, source="10.9.9.9:/export/backups"))
    failures = mount_consistency.consistency_failures(cfg)
    assert len(failures) == 1
    assert "10.9.9.9:/export/backups" in failures[0]
    assert NFS_SOURCE in failures[0]
    assert "update /etc/fstab on ALL nodes" in failures[0]
    assert "update-config" in failures[0]
    assert "BACKUP_REQUIRE_FSTAB_CONSISTENCY=false" in failures[0]


def test_changed_fstype_fails(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    _record_current_fstab(cfg)
    write_fstab(cfg, mount_line(cfg, fstype="nfs"))
    assert len(mount_consistency.consistency_failures(cfg)) == 1


def test_changed_options_fail(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    _record_current_fstab(cfg)
    write_fstab(cfg, mount_line(cfg, options="rw,soft"))
    failures = mount_consistency.consistency_failures(cfg)
    assert len(failures) == 1
    assert "rw,soft" in failures[0]


def test_recorded_without_recorded_by_renders_plain_label(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    _record_current_fstab(cfg)
    metadata = mount_consistency.metadata_path(cfg)
    assert metadata is not None
    text = metadata.read_text(encoding="utf-8").replace('"host-a"', '""')
    metadata.write_text(text, encoding="utf-8")
    write_fstab(cfg, "# gone\n")
    failures = mount_consistency.consistency_failures(cfg)
    assert len(failures) == 1
    assert "recorded by" not in failures[0]


def test_preflight_check_records_metadata_when_lock_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from libvirt_backup_system import preflight
    from tests.unit._preflight_helpers import make_config as make_preflight_config
    from tests.unit._preflight_helpers import stub_environment, write_password_file

    cfg = make_preflight_config(tmp_path)
    write_password_file(cfg)
    stub_environment(monkeypatch)
    write_fstab(cfg, mount_line(cfg))
    monkeypatch.setattr(preflight, "stamp_host_id_on_first_run", lambda _cfg: [])
    assert preflight.check(cfg, lock_held=True) == 0
    entry, recorded_by = mount_consistency.recorded_entry(cfg)
    assert entry is not None and entry.source == NFS_SOURCE
    assert recorded_by == "host-a"


def test_preflight_check_fails_on_divergent_fstab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from libvirt_backup_system import preflight
    from tests.unit._preflight_helpers import make_config as make_preflight_config
    from tests.unit._preflight_helpers import stub_environment, write_password_file

    cfg = make_preflight_config(tmp_path)
    write_password_file(cfg)
    stub_environment(monkeypatch)
    write_fstab(cfg, mount_line(cfg))
    mount_consistency.record_local_mount(cfg)
    write_fstab(cfg, mount_line(cfg, source="10.9.9.9:/export/backups"))
    failures, _vms, _kb = preflight.collect_check_failures(cfg)
    assert any("fstab entry for the BACKUP_PATH mount differs" in failure for failure in failures)


def test_collect_check_skips_consistency_when_booleans_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from libvirt_backup_system import preflight
    from tests.unit._preflight_helpers import make_config as make_preflight_config
    from tests.unit._preflight_helpers import stub_environment, write_password_file

    cfg = make_preflight_config(tmp_path)
    cfg.values["BACKUP_REQUIRE_FSTAB_CONSISTENCY"] = "banana"
    write_password_file(cfg)
    stub_environment(monkeypatch)
    monkeypatch.setattr(
        mount_consistency,
        "consistency_failures",
        lambda _cfg: pytest.fail("consistency must not run on invalid booleans"),
    )
    failures, _vms, _kb = preflight.collect_check_failures(cfg)
    assert "BACKUP_REQUIRE_FSTAB_CONSISTENCY must be a boolean value" in failures


def test_repo_creation_preflight_includes_consistency(tmp_path: Path) -> None:
    from libvirt_backup_system import preflight

    cfg = make_config(tmp_path)
    cfg.values["BACKUP_REQUIRE_NFS_MOUNT"] = "false"
    cfg.values["REQUIRE_ROOT"] = "false"
    write_fstab(cfg, mount_line(cfg))
    mount_consistency.record_local_mount(cfg)
    write_fstab(cfg, mount_line(cfg, source="10.9.9.9:/export/backups"))
    failures = preflight.repo_creation_failures(cfg)
    assert any("fstab entry for the BACKUP_PATH mount differs" in failure for failure in failures)


def test_update_shared_config_rerecords_mount_metadata(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.path.parent.mkdir(parents=True, exist_ok=True)
    cfg.path.write_text(f"BACKUP_PATH={cfg.get('BACKUP_PATH')}\n", encoding="utf-8")
    write_fstab(cfg, mount_line(cfg))
    mount_consistency.record_local_mount(cfg)
    write_fstab(cfg, mount_line(cfg, source="10.9.9.9:/export/backups"))
    assert config_sync.update_shared_config(cfg) == 0
    entry, _ = mount_consistency.recorded_entry(cfg)
    assert entry is not None and entry.source == "10.9.9.9:/export/backups"
