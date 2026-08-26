from __future__ import annotations

import json
from pathlib import Path

import pytest

from libvirt_backup_system import mount_consistency
from libvirt_backup_system.config import Config
from libvirt_backup_system.mount_consistency import MountEntry

NFS_SOURCE = "10.0.0.5:/export/backups"
NFS_OPTIONS = "rw,hard,timeo=600"


def make_config(tmp_path: Path, *, backup_path: str | None = None) -> Config:
    cfg = Config.load(prefix=str(tmp_path), apply_env_overrides=False)
    cfg.values["BACKUP_PATH"] = backup_path if backup_path is not None else str(tmp_path / "srv/backups")
    cfg.values["HOST_ID"] = "host-a"
    Path(cfg.values["BACKUP_PATH"] or str(tmp_path)).mkdir(parents=True, exist_ok=True)
    return cfg


def mount_line(cfg: Config, *, source: str = NFS_SOURCE, fstype: str = "nfs4", options: str = NFS_OPTIONS) -> str:
    return f"{source} {cfg.get('BACKUP_PATH')} {fstype} {options} 0 0\n"


def write_fstab(cfg: Config, text: str) -> None:
    path = mount_consistency.fstab_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_proc_mounts(cfg: Config, text: str) -> None:
    path = mount_consistency.proc_mounts_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- parsing -----------------------------------------------------------------


def test_parse_mount_table_skips_comments_and_short_lines() -> None:
    entries = mount_consistency.parse_mount_table(
        "# comment\n\nbroken line\n10.0.0.5:/export /mnt/backups/ nfs4 rw,hard 0 0\n"
    )
    assert entries == [MountEntry("10.0.0.5:/export", "/mnt/backups", "nfs4", ("rw", "hard"))]


def test_parse_mount_table_unescapes_octal_whitespace() -> None:
    entries = mount_consistency.parse_mount_table("/dev/sda1 /mnt/my\\040backups ext4 rw 0 0\n")
    assert entries[0].mount_point == "/mnt/my backups"


def test_parse_mount_table_root_mount_point_survives_normalization() -> None:
    entries = mount_consistency.parse_mount_table("/dev/root / ext4 rw 0 0\n")
    assert entries[0].mount_point == "/"


def test_mount_entry_render() -> None:
    entry = MountEntry(NFS_SOURCE, "/mnt/backups", "nfs4", ("rw", "hard"))
    assert entry.render() == f"{NFS_SOURCE} /mnt/backups nfs4 rw,hard"


# --- local state -------------------------------------------------------------


def test_local_mount_state_prefers_live_mount(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_proc_mounts(cfg, mount_line(cfg))
    # The unrelated rootfs line exercises the "entry does not match the
    # mount point" branch of the fstab lookup.
    write_fstab(cfg, "/dev/root / ext4 rw 0 0\n" + mount_line(cfg, options="rw,soft"))
    state = mount_consistency.local_mount_state(cfg)
    assert state.mount_point == cfg.get("BACKUP_PATH")
    assert state.live is not None and state.live.options == ("rw", "hard", "timeo=600")
    assert state.fstab is not None and state.fstab.options == ("rw", "soft")


def test_local_mount_state_falls_back_to_fstab_only(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    state = mount_consistency.local_mount_state(cfg)
    assert state.mount_point == cfg.get("BACKUP_PATH")
    assert state.live is None
    assert state.fstab is not None


def test_local_mount_state_no_tables(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    state = mount_consistency.local_mount_state(cfg)
    assert state == mount_consistency.LocalMountState(None, None, None)


def test_local_mount_state_matches_parent_mount_point(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    parent = str(Path(cfg.get("BACKUP_PATH")).parent)
    write_proc_mounts(cfg, f"{NFS_SOURCE} {parent} nfs4 rw 0 0\n")
    state = mount_consistency.local_mount_state(cfg)
    assert state.mount_point == parent


def test_local_mount_state_last_fstab_entry_wins(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg, source="10.0.0.4:/old") + mount_line(cfg))
    state = mount_consistency.local_mount_state(cfg)
    assert state.fstab is not None and state.fstab.source == NFS_SOURCE


# --- metadata read/write -----------------------------------------------------


def test_metadata_path_requires_backup_path(tmp_path: Path) -> None:
    cfg = make_config(tmp_path, backup_path="")
    assert mount_consistency.metadata_path(cfg) is None
    assert mount_consistency.recorded_entry(cfg) == (None, "")
    mount_consistency.record_local_mount(cfg)


def test_record_and_read_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    mount_consistency.record_local_mount(cfg)
    metadata = mount_consistency.metadata_path(cfg)
    assert metadata is not None and metadata.is_file()
    entry, recorded_by = mount_consistency.recorded_entry(cfg)
    assert entry == MountEntry(NFS_SOURCE, cfg.get("BACKUP_PATH"), "nfs4", ("rw", "hard", "timeo=600"))
    assert recorded_by == "host-a"
    assert "published mount metadata" in capsys.readouterr().out


def test_record_first_writer_wins_unless_overwrite(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    mount_consistency.record_local_mount(cfg)
    write_fstab(cfg, mount_line(cfg, source="10.9.9.9:/moved"))
    mount_consistency.record_local_mount(cfg)
    entry, _ = mount_consistency.recorded_entry(cfg)
    assert entry is not None and entry.source == NFS_SOURCE
    mount_consistency.record_local_mount(cfg, overwrite=True)
    entry, _ = mount_consistency.recorded_entry(cfg)
    assert entry is not None and entry.source == "10.9.9.9:/moved"


def test_record_skips_without_fstab_entry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = make_config(tmp_path)
    mount_consistency.record_local_mount(cfg)
    metadata = mount_consistency.metadata_path(cfg)
    assert metadata is not None and not metadata.exists()
    assert "mount metadata not published" in capsys.readouterr().out


def test_record_skips_root_mount_point(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, "/dev/root / ext4 rw 0 0\n")
    mount_consistency.record_local_mount(cfg)
    metadata = mount_consistency.metadata_path(cfg)
    assert metadata is not None and not metadata.exists()


def test_record_skips_when_disabled(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.values["BACKUP_REQUIRE_FSTAB_CONSISTENCY"] = "false"
    write_fstab(cfg, mount_line(cfg))
    mount_consistency.record_local_mount(cfg)
    metadata = mount_consistency.metadata_path(cfg)
    assert metadata is not None and not metadata.exists()


def test_record_write_failure_is_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = make_config(tmp_path)
    write_fstab(cfg, mount_line(cfg))
    metadata = mount_consistency.metadata_path(cfg)
    assert metadata is not None
    # A directory squatting on the temp path makes the atomic write fail.
    metadata.with_name(f".{metadata.name}.tmp").mkdir()
    mount_consistency.record_local_mount(cfg)
    assert "failed to publish mount metadata" in capsys.readouterr().err
    assert not metadata.exists()


def test_metadata_with_non_dict_mounts_treated_as_absent(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    metadata = mount_consistency.metadata_path(cfg)
    assert metadata is not None
    metadata.write_text(json.dumps({"version": 1, "mounts": ["bogus"]}), encoding="utf-8")
    assert mount_consistency.recorded_entry(cfg) == (None, "")
    write_fstab(cfg, mount_line(cfg))
    mount_consistency.record_local_mount(cfg)
    entry, _ = mount_consistency.recorded_entry(cfg)
    assert entry is not None


def test_corrupt_metadata_treated_as_absent(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    metadata = mount_consistency.metadata_path(cfg)
    assert metadata is not None
    metadata.write_text("{not json", encoding="utf-8")
    assert mount_consistency.recorded_entry(cfg) == (None, "")
    write_fstab(cfg, mount_line(cfg))
    mount_consistency.record_local_mount(cfg)
    entry, _ = mount_consistency.recorded_entry(cfg)
    assert entry is not None


def test_recorded_entry_rejects_malformed_records(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    metadata = mount_consistency.metadata_path(cfg)
    assert metadata is not None
    key = cfg.get("BACKUP_PATH")

    def store(record: object) -> None:
        metadata.write_text(json.dumps({"version": 1, "mounts": {key: record}}), encoding="utf-8")

    store({"source": 5, "mount_point": key, "fstype": "nfs4", "options": ["rw"]})
    assert mount_consistency.recorded_entry(cfg) == (None, "")
    store({"source": "x", "mount_point": key, "fstype": "nfs4", "options": "rw"})
    assert mount_consistency.recorded_entry(cfg) == (None, "")
    store({"source": "x", "mount_point": key, "fstype": "nfs4", "options": ["rw", 3]})
    assert mount_consistency.recorded_entry(cfg) == (None, "")
    store({"source": "x", "mount_point": key, "fstype": "nfs4", "options": ["rw"], "recorded_by": 9})
    entry, recorded_by = mount_consistency.recorded_entry(cfg)
    assert entry is not None
    assert recorded_by == ""
    store("not a dict")
    assert mount_consistency.recorded_entry(cfg) == (None, "")
