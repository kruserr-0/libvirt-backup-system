"""Domain-XML and destination-path helpers shared by restore flavors.

Split out of ``restore.py`` so the turnkey helpers can be reused by the
``temp-restore`` clone flow without dragging in the overwrite machinery.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .manifest import Manifest


def turnkey_disk_filename(target: str) -> str:
    safe_target = target.replace("/", "_")
    return f"{'disk' if safe_target in {'', '.', '..'} else safe_target}.qcow2"


def turnkey_dest_map(manifest: Manifest, staging: Path) -> dict[str, Path]:
    return {disk.target: staging / turnkey_disk_filename(disk.target) for disk in manifest.disks}


def rewrite_domain_disk_sources(domain_xml: str, dest_map: dict[str, Path]) -> str:
    """Rewrite restored file/block disk sources by ``<target dev=...>``."""
    root = ET.fromstring(domain_xml)  # noqa: S314
    for disk_el in root.findall(".//devices/disk"):
        target_el = disk_el.find("target")
        if target_el is None:
            continue
        target_dev = target_el.get("dev")
        if target_dev is None or target_dev not in dest_map:
            continue
        source_el = disk_el.find("source")
        if source_el is None:
            continue
        new_path = str(dest_map[target_dev])
        if "file" in source_el.attrib:
            source_el.set("file", new_path)
        elif "dev" in source_el.attrib:
            source_el.set("dev", new_path)
    return ET.tostring(root, encoding="unicode")
