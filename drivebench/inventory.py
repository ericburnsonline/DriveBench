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

COLUMNS = "NAME,KNAME,PATH,TYPE,SIZE,MODEL,VENDOR,SERIAL,WWN,TRAN,RO,RM,FSTYPE,LABEL,UUID,MOUNTPOINTS,PTTYPE,MAJ:MIN,ROTA"
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
        if shutil.which("lspci"):
            try:
                label = run(["lspci", "-s", path.name], timeout=4).strip()
            except InventoryError:
                pass
        driver_path = path / "driver"
        driver = driver_path.resolve().name if driver_path.exists() else "unknown"
        result[path.name] = {
            "id": controller_id,
            "address": path.name,
            "class": pci_class,
            "vendor_id": vendor,
            "device_id": device,
            "driver": driver,
            "label": label or f"PCI {pci_class} {vendor}:{device} ({driver})",
            "hosts": [],
        }
    return result


def _ancestor_pci(path: Path) -> str | None:
    try:
        parts = path.resolve().parts
    except OSError:
        return None
    # The most deeply nested PCI device is the attachment point.
    for part in reversed(parts):
        if BDF_RE.fullmatch(part) and (Path("/sys/bus/pci/devices") / part).exists():
            return part
    return None


def scsi_controllers() -> tuple[list[dict], dict[str, str]]:
    pci = _pci_devices()
    host_to_controller: dict[str, str] = {}
    host_base = Path("/sys/class/scsi_host")
    if host_base.is_dir():
        for entry in host_base.glob("host*"):
            address = _ancestor_pci(entry)
            if address in pci:
                pci[address]["hosts"].append(entry.name)
                host_to_controller[entry.name] = pci[address]["id"]
    controllers = sorted(pci.values(), key=lambda d: d["address"])
    for controller in controllers:
        controller["hosts"].sort()
    return controllers, host_to_controller


def udev_properties(device: str) -> dict[str, str]:
    if not shutil.which("udevadm"):
        return {}
    try:
        output = run(["udevadm", "info", "--query=property", "--name", device], timeout=5)
    except InventoryError:
        return {}
    properties = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            properties[key] = value
    return properties


def _usable_serial(value: str) -> bool:
    serial = slug(value)
    return bool(serial and len(serial) <= 128 and serial.lower() not in
                {"unknown", "none", "n/a", "not available", "00000000", "0"}
                and any(ch.isalnum() and ch != '0' for ch in serial))


_SERIAL_CACHE: dict[tuple, tuple[str, str]] = {}


def probe_device_serial(device: str, *, bus: str = "", sudo: bool = False,
                        diskseq: str = "", size: int = 0, model: str = "") -> tuple[str, str]:
    """Try drive-native identity, read-only. USB requires ATA pass-through.

    The result is the physical drive's reported serial, not a USB bridge's
    ID_SERIAL. Failure is normal on controllers requiring privilege or old disks.
    """
    if not re.fullmatch(r'/dev/(?:sd[a-z]+|hd[a-z]+|nvme\d+n\d+)', device):
        return '', ''
    usb = bus.lower() == 'usb'
    # A Linux diskseq changes when media is re-enumerated. Never cache an
    # inquiry across replacement when that kernel generation ID is missing.
    cache_key = (device, diskseq, size, model, usb) if diskseq else None
    if cache_key and cache_key in _SERIAL_CACHE:
        return _SERIAL_CACHE[cache_key]
    prefix = ['sudo'] if sudo else []
    if shutil.which('smartctl'):
        argv = prefix + ['smartctl', '-i', '-j'] + (['-d', 'sat'] if usb else []) + [device]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
            raw = json.loads(proc.stdout)
            # A USB adapter's own emulated SCSI serial is not a disk serial.
            protocol = str((raw.get('device') or {}).get('protocol') or '').lower()
            serial = slug(raw.get('serial_number'))
            if _usable_serial(serial) and (not usb or protocol == 'ata'):
                response = (serial, 'smartctl ATA pass-through' if usb else 'smartctl device inquiry')
                if cache_key:
                    _SERIAL_CACHE[cache_key] = response
                return response
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
            pass
    if not usb and shutil.which('sg_inq'):
        try:
            proc = subprocess.run(prefix + ['sg_inq', '--page=0x80', device],
                                  capture_output=True, text=True, timeout=8, check=False)
            if proc.returncode == 0:
                match = re.search(r'Unit serial number:\s*(.+)', proc.stdout, flags=re.IGNORECASE)
                if match and _usable_serial(match.group(1)):
                    response = (slug(match.group(1)), 'SCSI VPD page 0x80')
                    if cache_key:
                        _SERIAL_CACHE[cache_key] = response
                    return response
        except (OSError, subprocess.TimeoutExpired):
            pass
    return '', ''


def drive_identity(disk: dict, props: dict[str, str]) -> tuple[str | None, str, str]:
    """Return (identity-key, confidence, reason).

    USB bridge serials can identify the adapter rather than the disk. Never
    associate historical wipe results using such an identifier alone.
    """
    wwn = slug(props.get("ID_WWN_WITH_EXTENSION") or props.get("ID_WWN") or disk.get("wwn"))
    model = slug(disk.get("model") or props.get("ID_MODEL"))
    vendor = slug(disk.get("vendor") or props.get("ID_VENDOR"))
    size = safe_number(disk.get("size"))
    bus = slug(disk.get("tran") or props.get("ID_BUS")).lower()
    actual_usb = bus == "usb" or props.get("ID_BUS", "").lower() == "usb"
    # ID_ATA_SERIAL/ID_SCSI_SERIAL, if genuinely supplied by the device, is
    # preferable to an identifier assigned to an external bridge.
    native_serial = slug(props.get("ID_ATA_SERIAL") or ("" if actual_usb else props.get("ID_SCSI_SERIAL")))
    serial = slug(disk.get("serial") or ("" if actual_usb else props.get("ID_SERIAL_SHORT")))
    if wwn and wwn.strip("0x0.-_") and (not actual_usb or props.get("ID_ATA_SERIAL")):
        material = ["wwn", wwn.lower(), size]
        return _hash_id(material), "strong", "WWN"
    if native_serial and native_serial.strip("0- "):
        return _hash_id(["native-serial", vendor.lower(), model.lower(), native_serial, size]), "strong", "device-reported serial"
    if serial and not actual_usb and serial.strip("0- "):
        return _hash_id(["serial", vendor.lower(), model.lower(), serial, size]), "strong", "device serial"
    return None, "unverified", "no trustworthy disk identity; USB bridge IDs may belong to the adapter" if actual_usb else "no trustworthy serial or WWN"


def _hash_id(parts: list[Any]) -> str:
    payload = json.dumps(parts, separators=(",", ":"), ensure_ascii=True)
    return "drive:" + hashlib.sha256(payload.encode()).hexdigest()


def _slot_for_disk(kname: str, controller_id: str | None) -> tuple[str | None, str | None]:
    path = Path("/sys/class/block") / kname / "device"
    if not path.exists():
        return None, None
    resolved = str(path.resolve())
    hctl = HCTL_RE.search(resolved)
    hctl_value = ":".join(hctl.groups()) if hctl else None
    usb = USB_PORT_RE.search(resolved)
    if controller_id and hctl:
        # SCSI host numbers vary across boots; retain C:T:L only.
        _, channel, target, lun = hctl.groups()
        return f"scsi-slot:{controller_id}:{channel}:{target}:{lun}", hctl_value
    if usb and hctl:
        _, _, target, lun = hctl.groups()
        return f"usb-slot:{usb.group(1)}:{target}:{lun}", hctl_value
    return None, hctl_value


def _root_majmin() -> str:
    if shutil.which("findmnt"):
        try:
            return run(["findmnt", "-n", "-o", "MAJ:MIN,ROTA", "--target", "/"], timeout=5).strip().splitlines()[0]
        except (InventoryError, IndexError):
            pass
    try:
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            fields = line.split(" ")
            if len(fields) > 4 and fields[4] == "/":
                return fields[2]
    except OSError:
        pass
    return ""


def system_protection(disks: list[dict]) -> tuple[set[str], list[str]]:
    """Work upward from root/boot/swap, including stacked LVM/RAID children.

    Fails closed when the root backing disk cannot be resolved.
    """
    protected = set()
    root_major = _root_majmin()
    root_resolved = False
    for disk in disks:
        all_nodes = list(flatten(disk["tree"]))
        reason = []
        for n in all_nodes:
            points = mounted_at(n)
            is_root = "/" in points or (root_major and str(n.get("maj:min") or "") == root_major)
            if is_root:
                root_resolved = True
                reason.append("root filesystem")
            if any(p in ("/boot", "/boot/efi") for p in points):
                reason.append("boot filesystem")
            if "[SWAP]" in points:
                reason.append("active swap")
        if reason:
            disk["system_reasons"] = sorted(set(reason))
            protected.add(disk["path"])
    # Many swap devices show as [SWAP] in lsblk. Also check /proc/swaps
    # for systems where mountpoint information is absent.
    try:
        swaps = Path("/proc/swaps").read_text().splitlines()[1:]
        swap_paths = {os.path.realpath(x.split()[0]) for x in swaps if x.split()}
        for disk in disks:
            if any(os.path.realpath(str(n.get("path") or "")) in swap_paths for n in flatten(disk["tree"])):
                protected.add(disk["path"])
                disk.setdefault("system_reasons", []).append("active swap")
    except OSError:
        pass
    warnings = []
    if not root_resolved:
        warnings.append(f"Cannot prove which physical disk backs / (MAJ:MIN={root_major or 'unknown'}). All drives are conservatively protected.")
        for disk in disks:
            disk.setdefault("system_reasons", []).append("root mapping unresolved")
            protected.add(disk["path"])
    return protected, warnings


def discover(*, sudo_probe_path: str | None = None) -> dict:
    if os.name != "posix" or not Path("/sys/class/block").exists():
        raise InventoryError("DriveBench milestone 1 requires Linux with sysfs")
    output = run(["lsblk", "--json", "--bytes", "--paths", "--tree", "--output", COLUMNS])
    try:
        roots = json.loads(output)["blockdevices"]
    except (ValueError, KeyError, TypeError) as exc:
        raise InventoryError("Cannot parse lsblk JSON output") from exc
    controllers, hostmap = scsi_controllers()
    disks = []
    seen_paths = set()
    for root in roots:
        if root.get("type") != "disk":
            continue
        path = str(root.get("path") or root.get("name") or "")
        if not path.startswith("/dev/") or path in seen_paths:
            continue
        seen_paths.add(path)
        kname = Path(str(root.get("kname") or path)).name
        device_path = Path("/sys/class/block") / kname / "device"
        sys_device = str(device_path.resolve()) if device_path.exists() else ""
        host_match = re.findall(r"/host(\d+)(?:/|$)", sys_device)
        host = "host" + host_match[-1] if host_match else None
        cid = hostmap.get(host) if host else None
        props = udev_properties(path)
        identity, confidence, reason = drive_identity(root, props)
        serial_source = 'lsblk/udev' if slug(root.get('serial')) else 'unavailable'
        # The normal scan never prompts for sudo. One explicitly selected drive
        # can be retried with sudo from the identification menu.
        if not identity:
            native, source = probe_device_serial(path, bus=slug(root.get('tran') or props.get('ID_BUS')),
                                                 sudo=(sudo_probe_path == path),
                                                 diskseq=read_text(Path('/sys/class/block') / kname / 'diskseq'),
                                                 size=safe_number(root.get('size')), model=slug(root.get('model')))
            if native:
                root = dict(root)
                root['serial'] = native
                serial_source = source
                enhanced_props = dict(props)
                if str(root.get('tran') or props.get('ID_BUS') or '').lower() == 'usb':
                    enhanced_props['ID_ATA_SERIAL'] = native
                else:
                    enhanced_props['ID_SCSI_SERIAL'] = native
                identity, confidence, reason = drive_identity(root, enhanced_props)
        slot, hctl = _slot_for_disk(kname, cid)
        partitions = []
        for n in flatten(root):
            if n is root:
                continue
            partitions.append({
                "path": n.get("path") or n.get("name"), "kname": n.get("kname"),
                "type": n.get("type"), "size": safe_number(n.get("size")),
                "fstype": n.get("fstype"), "label": n.get("label"),
                "uuid": n.get("uuid"), "majmin": str(n.get("maj:min") or ""), "mountpoints": mounted_at(n),
            })
        mounted = sorted({p for n in flatten(root) for p in mounted_at(n)})
        disks.append({
            "path": path, "kname": kname, "majmin": str(root.get("maj:min") or ""),
            "diskseq": read_text(Path("/sys/class/block") / kname / "diskseq") or None,
            "rotational": bool(safe_number(root.get("rota"))) if root.get("rota") is not None else None, "model": slug(root.get("model")),
            "vendor": slug(root.get("vendor")), "serial": slug(root.get("serial")),
            "serial_source": serial_source,
            "wwn": slug(root.get("wwn")), "size": safe_number(root.get("size")),
            "tran": slug(root.get("tran")) or ("SCSI" if cid else "unknown"),
            "pttype": root.get("pttype"), "fstype": root.get("fstype"), "readonly": bool(safe_number(root.get("ro"))),
            "removable": bool(safe_number(root.get("rm"))), "identity": identity,
            "confidence": confidence, "identity_reason": reason,
            "controller_id": cid, "scsi_host": host, "hctl": hctl,
            "slot": slot, "partitions": partitions, "mountpoints": mounted,
            "tree": root, "system_reasons": [],
        })
    _, warnings = system_protection(disks)
    identities: dict[str, int] = {}
    for disk in disks:
        if disk["identity"]:
            identities[disk["identity"]] = identities.get(disk["identity"], 0) + 1
    for disk in disks:
        if disk["identity"] and identities[disk["identity"]] > 1:
            disk["confidence"] = "unverified"
            disk["identity_reason"] = "duplicate identity visible on multiple disks"
            disk["identity"] = None
            warnings.append(f"Duplicate hardware identity detected for {disk['path']}; historical status not automatically linked")
    warnings.extend(scsi_alias_warnings(disks))
    return {"controllers": controllers, "disks": disks, "warnings": sorted(set(warnings))}


def scsi_alias_warnings(disks: list[dict]) -> list[str]:
    """Identify mass same-model enumeration on multiple targets, not assert duplicates.

    Multiple identical real disks can exist. Only flag the highly unusual
    pattern of 8+ target IDs on the same controller/channel and same model/size.
    """
    groups: dict[tuple, set[int]] = {}
    for d in disks:
        hctl = d.get('hctl')
        if not hctl or not d.get('scsi_host') or not d.get('model'):
            continue
        try:
            host, channel, target, lun = (int(x) for x in hctl.split(':'))
        except (TypeError, ValueError):
            continue
        if lun != 0:
            continue
        key = (host, channel, d.get('model'), d.get('size'))
        groups.setdefault(key, set()).add(target)
    warnings = []
    for (host, channel, model, size), ids in groups.items():
        if len(ids) >= 8:
            target_str = ', '.join(map(str, sorted(ids)))
            warnings.append(
                f'Suspicious SCSI enumeration: {model} ({sizestr(size)}) appears '
                f'at {len(ids)} target IDs on host {host}, channel {channel} '
                f'({target_str}). Possible drive/controller SCSI ID conflict or '
                'jumper/cabling problem. Power off before checking. '
                'Do not mount/write until resolved.')
    return warnings
