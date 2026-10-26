"""DriveBench 0.2: interactive terminal menus only; no command-line subcommands."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from . import deps, inventory as inv, mounts, store


class UserError(RuntimeError):
    pass


def status_reasons(disk: dict, config: dict) -> list[str]:
    reasons = []
    for reason in disk.get("system_reasons", []):
        reasons.append("SYSTEM: " + reason)
    if disk.get("identity") and disk["identity"] in config.get("ignored_drives", []):
        reasons.append("IGNORED: drive identity")
    if disk.get("slot") and disk["slot"] in config.get("ignored_slots", []):
        reasons.append("IGNORED: connection slot")
    if disk.get("controller_id") and disk["controller_id"] in config.get("ignored_controllers", []):
        reasons.append("IGNORED: SCSI/RAID controller")
    if disk.get("readonly"):
        reasons.append("READ-ONLY device")
    if disk.get("mountpoints"):
        reasons.append("MOUNTED: " + ", ".join(disk["mountpoints"]))
    if disk.get("confidence") != "strong":
        reasons.append("IDENTITY UNVERIFIED: history not linked")
    return reasons


def snapshot() -> dict:
    result = inv.discover()
    config = store.read_config()
    seen = store.remember_disks(result["disks"])
    for disk in result["disks"]:
        entry = seen.get(disk["path"], {})
        disk["record_id"] = entry.get("id")
        disk["status"] = entry.get("status", "UNVERIFIED")
        disk["reasons"] = status_reasons(disk, config)
        disk.pop("tree", None)
    for ctl in result["controllers"]:
        ctl["ignored"] = ctl["id"] in config.get("ignored_controllers", [])
    return result


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _pick(items: list, heading: str, describe) -> tuple[int, object] | None:
    print("\n" + heading)
    for i, item in enumerate(items, 1):
        print(f" {i:>2}. {describe(item)}")
    if not items:
        print("  (none)")
        return None
    print("  0. Back")
    raw = _ask("Selection: ")
    if raw in ("", "0"):
        return None
    if not raw.isdecimal() or not (1 <= int(raw) <= len(items)):
        raise UserError("Select a displayed number")
    index = int(raw) - 1
    return index, items[index]


def _disk_desc(disk: dict) -> str:
    marks = []
    if any(r.startswith("SYSTEM:") for r in disk["reasons"]):
        marks.append("SYSTEM")
    if any(r.startswith("IGNORED:") for r in disk["reasons"]):
        marks.append("IGNORED")
    return (f"{disk['path']}  {inv.sizestr(disk['size'])}  {disk['model'] or 'unknown'}  "
            f"{disk['record_id'] or 'NO STABLE ID'}  [{disk['status']}]"
            + ("  (" + ", ".join(marks) + ")" if marks else ""))


def _part_desc(part: dict) -> str:
    mounts_here = ", ".join(part.get("mountpoints") or []) or "not mounted"
    return (f"{part['path']}  {inv.sizestr(part['size'])} "
            f"{part.get('fstype') or 'unknown FS'}  [{mounts_here}]")


def _show_disks(result: dict) -> None:
    print("\nDISKS AND PARTITIONS")
    for warning in result["warnings"]:
        print(" WARNING:", warning)
    if not result["disks"]:
        print(" No disks found")
    for i, disk in enumerate(result["disks"], 1):
        print(f" {i}. {_disk_desc(disk)}")
