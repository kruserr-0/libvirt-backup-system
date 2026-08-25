"""Tests for the temp-restore domain-XML surgery."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from libvirt_backup_system.temp_restore_xml import rewrite_for_temp

NVRAM_DIR = Path("/stage")
DEST_MAP = {"vda": Path("/stage/vda.qcow2"), "vdb": Path("/stage/vdb.qcow2")}

FULL_XML = """\
<domain type='kvm'>
  <name>myvm</name>
  <uuid>aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa</uuid>
  <os>
    <type arch='x86_64'>hvm</type>
    <nvram template='/usr/share/OVMF/OVMF_VARS.fd'>/var/lib/libvirt/qemu/nvram/myvm_VARS.fd</nvram>
  </os>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='/var/lib/libvirt/images/myvm-vda.qcow2'/>
      <backingStore/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='block' device='disk'>
      <source dev='/dev/vg0/myvm-data'/>
      <target dev='vdb' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='/isos/tools.iso'/>
      <target dev='sda' bus='sata'/>
    </disk>
    <interface type='network'>
      <mac address='52:54:00:11:22:33'/>
      <target dev='vnet7'/>
      <model type='virtio'/>
    </interface>
    <interface type='user'/>
    <graphics type='vnc' port='5900' tlsPort='5901' websocket='5700' autoport='no'/>
    <hostdev mode='subsystem' type='pci'/>
    <hostdev mode='subsystem' type='usb'/>
    <channel type='unix'>
      <source mode='bind' path='/var/lib/libvirt/qemu/channel/target/myvm.agent'/>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <channel type='unix'>
      <source mode='bind'/>
    </channel>
  </devices>
</domain>
"""


def _rewritten_root() -> ET.Element:
    rewritten = rewrite_for_temp(FULL_XML, DEST_MAP, NVRAM_DIR)
    assert rewritten is not None
    return ET.fromstring(rewritten)


def test_rewrite_points_disks_at_clone_files_as_qcow2() -> None:
    root = _rewritten_root()
    disks = {d.find("target").get("dev"): d for d in root.findall(".//devices/disk")}  # type: ignore[union-attr]
    vda = disks["vda"]
    assert vda.get("type") == "file"
    assert vda.find("driver").get("type") == "qcow2"  # type: ignore[union-attr]
    assert vda.find("source").get("file") == "/stage/vda.qcow2"  # type: ignore[union-attr]
    assert vda.find("backingStore") is None


def test_rewrite_converts_block_disk_to_qcow2_file() -> None:
    root = _rewritten_root()
    disks = {d.find("target").get("dev"): d for d in root.findall(".//devices/disk")}  # type: ignore[union-attr]
    vdb = disks["vdb"]
    assert vdb.get("type") == "file"
    # The block disk had no <driver>; one must be created for the qcow2 file.
    assert vdb.find("driver").get("name") == "qemu"  # type: ignore[union-attr]
    assert vdb.find("driver").get("type") == "qcow2"  # type: ignore[union-attr]
    source = vdb.find("source")
    assert source is not None
    assert source.get("dev") is None
    assert source.get("file") == "/stage/vdb.qcow2"


def test_rewrite_leaves_disks_outside_dest_map_alone() -> None:
    root = _rewritten_root()
    disks = {d.find("target").get("dev"): d for d in root.findall(".//devices/disk")}  # type: ignore[union-attr]
    cdrom = disks["sda"]
    assert cdrom.find("source").get("file") == "/isos/tools.iso"  # type: ignore[union-attr]
    assert cdrom.find("driver").get("type") == "raw"  # type: ignore[union-attr]


def test_rewrite_strips_interface_mac_and_target() -> None:
    root = _rewritten_root()
    interfaces = root.findall(".//devices/interface")
    assert len(interfaces) == 2
    for interface in interfaces:
        assert interface.find("mac") is None
        assert interface.find("target") is None
    # Non-identity children survive.
    assert interfaces[0].find("model") is not None


def test_rewrite_relaxes_graphics_ports() -> None:
    graphics = _rewritten_root().find(".//devices/graphics")
    assert graphics is not None
    assert graphics.get("autoport") == "yes"
    assert graphics.get("port") is None
    assert graphics.get("tlsPort") is None
    assert graphics.get("websocket") is None


def test_rewrite_drops_hostdevs_and_logs(capsys: pytest.CaptureFixture[str]) -> None:
    root = _rewritten_root()
    assert root.findall(".//devices/hostdev") == []
    assert "temp restore dropped host passthrough devices" in capsys.readouterr().out


def test_rewrite_strips_channel_socket_paths() -> None:
    root = _rewritten_root()
    for source in root.findall(".//devices/channel/source"):
        assert source.get("path") is None


def test_rewrite_relocates_nvram_keeping_template() -> None:
    nvram = _rewritten_root().find("./os/nvram")
    assert nvram is not None
    assert nvram.text == "/stage/myvm_VARS.fd"
    assert nvram.get("template") == "/usr/share/OVMF/OVMF_VARS.fd"


def test_rewrite_rejects_unparseable_xml(capsys: pytest.CaptureFixture[str]) -> None:
    assert rewrite_for_temp("<domain", DEST_MAP, NVRAM_DIR) is None
    assert "temp restore domain XML is unparseable" in capsys.readouterr().err


def test_rewrite_rejects_non_domain_root(capsys: pytest.CaptureFixture[str]) -> None:
    assert rewrite_for_temp("<network/>", DEST_MAP, NVRAM_DIR) is None
    assert "temp restore XML is not a libvirt domain" in capsys.readouterr().err


def test_rewrite_survives_minimal_domain() -> None:
    rewritten = rewrite_for_temp("<domain type='kvm'><name>myvm</name></domain>", DEST_MAP, NVRAM_DIR)
    assert rewritten is not None
    assert "<name>myvm</name>" in rewritten


@pytest.mark.parametrize(
    "disk_xml",
    [
        "<disk type='file'><source file='/x'/></disk>",
        "<disk type='file'><target bus='virtio'/><source file='/x'/></disk>",
    ],
    ids=["no-target", "target-without-dev"],
)
def test_rewrite_skips_disks_without_usable_target(disk_xml: str) -> None:
    xml = f"<domain><devices>{disk_xml}</devices></domain>"
    rewritten = rewrite_for_temp(xml, DEST_MAP, NVRAM_DIR)
    assert rewritten is not None
    source = ET.fromstring(rewritten).find(".//devices/disk/source")
    assert source is not None
    assert source.get("file") == "/x"


def test_rewrite_creates_source_element_when_missing() -> None:
    xml = "<domain><devices><disk type='file'><target dev='vda'/></disk></devices></domain>"
    rewritten = rewrite_for_temp(xml, DEST_MAP, NVRAM_DIR)
    assert rewritten is not None
    source = ET.fromstring(rewritten).find(".//devices/disk/source")
    assert source is not None
    assert source.get("file") == "/stage/vda.qcow2"


@pytest.mark.parametrize(
    "nvram_xml",
    ["<nvram/>", "<nvram>   </nvram>"],
    ids=["empty", "whitespace"],
)
def test_rewrite_leaves_empty_nvram_alone(nvram_xml: str) -> None:
    xml = f"<domain><os>{nvram_xml}</os></domain>"
    rewritten = rewrite_for_temp(xml, DEST_MAP, NVRAM_DIR)
    assert rewritten is not None
    nvram = ET.fromstring(rewritten).find("./os/nvram")
    assert nvram is not None
    assert nvram.text in {None, "   "}
