"""Read-only Linux storage inventory. No commands in this module write to block devices."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

COLUMNS = "NAME,KNAME,PATH,TYPE,SIZE,MODEL,VENDOR,SERIAL,WWN,TRAN,RO,RM,FSTYPE,LABEL,UUID,MOUNTPOINTS,PTTYPE,MAJ:MIN"
BDF_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
HCTL_RE = re.compile(r"/(\d+):(\d+):(\d+):(\d+)(?:/|$)")
USB_PORT_RE = re.compile(r"/(\d+-\d+(?:\.\d+)*):\d+\.\d+(?:/|$)")
SCSI_PCI_CLASSES = {"0100", "0104", "0107"}  # SCSI, RAID, SAS


class InventoryError(RuntimeError):
    pass


def run(argv: list[str], timeout: int = 15) -> str:
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InventoryError(f"Cannot execute {' '.join(argv)}: {exc}") from exc
    if cp.returncode:
        raise InventoryError(f"{' '.join(argv)} failed: {(cp.stderr or cp.stdout).strip()}")
    return cp.stdout


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def slug(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def sizestr(size: int) -> str:
    if size <= 0:
        return "unknown"
    for unit, scale in [("TiB", 1024**4), ("GiB", 1024**3), ("MiB", 1024**2)]:
        if size >= scale:
            return f"{size / scale:.2f} {unit}"
    return f"{size:,} bytes"


def safe_number(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def mounted_at(node: dict) -> list[str]:
    value = node.get("mountpoints")
    if isinstance(value, list):
        points = [str(p) for p in value if p]
    elif isinstance(value, str):
        points = [x.strip() for x in value.splitlines() if x.strip()]
    else:
        points = []
    if not points and node.get("mountpoint"):
        points = [str(node["mountpoint"])]
    return sorted(set(points))


def flatten(node: dict):
    yield node
    for child in node.get("children") or []:
        yield from flatten(child)


def _pci_devices() -> dict[str, dict]:
    result = {}
    base = Path("/sys/bus/pci/devices")
    if not base.is_dir():
        return result
    for path in base.iterdir():
        pci_class = read_text(path / "class").lower().removeprefix("0x")[:4]
        if pci_class not in SCSI_PCI_CLASSES:
            continue
        vendor = read_text(path / "vendor").lower()
        device = read_text(path / "device").lower()
        subvendor = read_text(path / "subsystem_vendor").lower()
        subdevice = read_text(path / "subsystem_device").lower()
        # Both the address and PCI fingerprint participate. A replacement card
        # at the same address will not silently inherit an exclusion.
        controller_id = f"pci:{path.name}:{vendor}:{device}:{subvendor}:{subdevice}"
        label = ""
