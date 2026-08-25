"""Tests for ``restore_xml.rewrite_domain_disk_sources``.

Moved out of ``test_restore_disk_ops.py`` when the rewrite helpers moved to
``restore_xml.py`` (shared between the overwrite/turnkey restore and the
temp-restore clone flow).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from libvirt_backup_system.restore_xml import rewrite_domain_disk_sources


def test_rewrite_domain_disk_sources_rewrites_file_attr() -> None:
    xml = (
        "<domain type='kvm'>"
        "  <devices>"
        "    <disk type='file' device='disk'>"
        "      <source file='/old/path.qcow2'/>"
        "      <target dev='vda' bus='virtio'/>"
        "    </disk>"
        "  </devices>"
        "</domain>"
    )
    rewritten = rewrite_domain_disk_sources(xml, {"vda": Path("/new/restored.qcow2")})
    root = ET.fromstring(rewritten)
    source = root.find(".//devices/disk/source")
    assert source is not None
    assert source.get("file") == "/new/restored.qcow2"


def test_rewrite_domain_disk_sources_rewrites_dev_attr() -> None:
    xml = (
        "<domain type='kvm'><devices>"
        "<disk type='block' device='disk'>"
        "<source dev='/dev/old'/>"
        "<target dev='vdb' bus='virtio'/>"
        "</disk>"
        "</devices></domain>"
    )
    rewritten = rewrite_domain_disk_sources(xml, {"vdb": Path("/stage/vdb.qcow2")})
    root = ET.fromstring(rewritten)
    source = root.find(".//devices/disk/source")
    assert source is not None
    assert source.get("dev") == "/stage/vdb.qcow2"
    assert "file" not in source.attrib


def test_rewrite_domain_disk_sources_skips_disks_not_in_map() -> None:
    """A disk whose target dev is absent from the map keeps its old source path.

    This is defensive: the manifest can carry CDROM / passthrough disks
    that we never snapshot, so we should never accidentally relocate them.
    """
    xml = (
        "<domain><devices>"
        "<disk type='file'><source file='/keep/me.iso'/><target dev='hda'/></disk>"
        "<disk type='file'><source file='/old.qcow2'/><target dev='vda'/></disk>"
        "</devices></domain>"
    )
    rewritten = rewrite_domain_disk_sources(xml, {"vda": Path("/new.qcow2")})
    root = ET.fromstring(rewritten)
    targets = root.findall(".//disk/target")
    srcs = root.findall(".//disk/source")
    sources = {t.get("dev"): s for t, s in zip(targets, srcs, strict=True)}
    assert sources["hda"].get("file") == "/keep/me.iso"
    assert sources["vda"].get("file") == "/new.qcow2"


def test_rewrite_domain_disk_sources_skips_disks_without_target() -> None:
    """Disks without a ``<target>`` element are left alone (defensive)."""
    xml = "<domain><devices><disk><source file='/x'/></disk></devices></domain>"
    rewritten = rewrite_domain_disk_sources(xml, {"vda": Path("/y")})
    root = ET.fromstring(rewritten)
    source = root.find(".//disk/source")
    assert source is not None
    assert source.get("file") == "/x"


def test_rewrite_domain_disk_sources_skips_disks_without_target_dev() -> None:
    """``<target>`` with no ``dev=`` attr is unusable as a key, so we skip it."""
    xml = "<domain><devices><disk><target bus='virtio'/><source file='/x'/></disk></devices></domain>"
    rewritten = rewrite_domain_disk_sources(xml, {"vda": Path("/y")})
    root = ET.fromstring(rewritten)
    source = root.find(".//disk/source")
    assert source is not None
    assert source.get("file") == "/x"


def test_rewrite_domain_disk_sources_skips_disks_without_source_element() -> None:
    """A target-only disk (no ``<source>``) is left untouched."""
    xml = "<domain><devices><disk><target dev='vda'/></disk></devices></domain>"
    rewritten = rewrite_domain_disk_sources(xml, {"vda": Path("/y")})
    root = ET.fromstring(rewritten)
    disk = root.find(".//disk")
    assert disk is not None
    assert disk.find("source") is None


def test_rewrite_domain_disk_sources_skips_unknown_source_form() -> None:
    """A ``<source>`` element with neither ``file=`` nor ``dev=`` is left alone.

    Volume / network sources go through other libvirt code paths; we make
    no claim about restoring them and so should not mangle them.
    """
    xml = (
        "<domain><devices>"
        "<disk type='volume'><target dev='vda'/>"
        "<source pool='p' volume='v'/>"
        "</disk>"
        "</devices></domain>"
    )
    rewritten = rewrite_domain_disk_sources(xml, {"vda": Path("/y")})
    root = ET.fromstring(rewritten)
    source = root.find(".//disk/source")
    assert source is not None
    assert source.get("pool") == "p"
    assert source.get("volume") == "v"
    assert "file" not in source.attrib
    assert "dev" not in source.attrib
