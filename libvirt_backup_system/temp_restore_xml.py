"""Domain-XML surgery that lets a restored clone run beside the original VM.

The backed-up domain XML describes the *original* VM, so defining it verbatim
would collide with the running production domain on every shared resource.
``rewrite_for_temp`` strips or rewrites everything that must be unique per
running domain; the fresh ``<name>``/``<uuid>`` are applied afterwards by
``define_restored_domain``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .logging_json import event


def rewrite_for_temp(domain_xml: str, dest_map: dict[str, Path], nvram_dir: Path) -> str | None:
    try:
        root = ET.fromstring(domain_xml)  # noqa: S314
    except ET.ParseError as exc:
        event("error", "temp restore domain XML is unparseable", error=str(exc))
        return None
    if root.tag != "domain":
        event("error", "temp restore XML is not a libvirt domain", root=root.tag)
        return None
    _rewrite_disk_sources(root, dest_map)
    _strip_interface_identity(root)
    _relax_graphics_ports(root)
    _detach_host_devices(root)
    _strip_channel_paths(root)
    _rewrite_nvram(root, nvram_dir)
    return ET.tostring(root, encoding="unicode")


def _rewrite_disk_sources(root: ET.Element, dest_map: dict[str, Path]) -> None:
    """Point every restored disk at its clone-local qcow2 file.

    Unlike the overwrite path this also forces ``type='file'`` and a qcow2
    driver: a source disk that lived on a block device (LVM, iSCSI) is
    restored as a qcow2 *file* for the clone, so the element form has to
    change with it.
    """
    for disk_el in root.findall(".//devices/disk"):
        target_el = disk_el.find("target")
        if target_el is None:
            continue
        target_dev = target_el.get("dev")
        if target_dev is None or target_dev not in dest_map:
            continue
        disk_el.set("type", "file")
        driver_el = disk_el.find("driver")
        if driver_el is None:
            driver_el = ET.SubElement(disk_el, "driver")
            driver_el.set("name", "qemu")
        driver_el.set("type", "qcow2")
        source_el = disk_el.find("source")
        if source_el is None:
            source_el = ET.SubElement(disk_el, "source")
        source_el.attrib.pop("dev", None)
        source_el.set("file", str(dest_map[target_dev]))
        backing_el = disk_el.find("backingStore")
        if backing_el is not None:
            # Live dumpxml carries runtime backing-chain info that would
            # point the clone back at the original's files.
            disk_el.remove(backing_el)


def _strip_interface_identity(root: ET.Element) -> None:
    """Drop MACs and tap names so libvirt assigns fresh ones to the clone."""
    for interface_el in root.findall(".//devices/interface"):
        for tag in ("mac", "target"):
            child = interface_el.find(tag)
            if child is not None:
                interface_el.remove(child)


def _relax_graphics_ports(root: ET.Element) -> None:
    """Autoport every graphics device so VNC/SPICE ports cannot collide."""
    for graphics_el in root.findall(".//devices/graphics"):
        graphics_el.set("autoport", "yes")
        for attr in ("port", "tlsPort", "websocket"):
            graphics_el.attrib.pop(attr, None)


def _detach_host_devices(root: ET.Element) -> None:
    """Remove <hostdev> passthrough: the original VM still owns the hardware."""
    for devices_el in root.findall(".//devices"):
        hostdevs = devices_el.findall("hostdev")
        for hostdev_el in hostdevs:
            devices_el.remove(hostdev_el)
        if hostdevs:
            event("info", "temp restore dropped host passthrough devices", count=len(hostdevs))


def _strip_channel_paths(root: ET.Element) -> None:
    """Drop explicit channel socket paths (e.g. qemu-ga) so they regenerate."""
    for source_el in root.findall(".//devices/channel/source"):
        source_el.attrib.pop("path", None)


def _rewrite_nvram(root: ET.Element, nvram_dir: Path) -> None:
    """Relocate UEFI nvram next to the clone so the original's file is safe."""
    nvram_el = root.find("./os/nvram")
    if nvram_el is None or nvram_el.text is None or not nvram_el.text.strip():
        return
    nvram_el.text = str(nvram_dir / Path(nvram_el.text.strip()).name)
