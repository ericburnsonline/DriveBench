"""Read-only mounting of a selected *partition*. Never formats or writes to disks.

The live kernel mount table, not stored state, determines what is mounted.
The registry records ownership/history, never grants authorization by itself.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from . import store

MOUNT_BASE = Path("/mnt/drivebench")
_OCTAL = re.compile(r"\\([0-7]{3})")
_FS = re.compile(r"^[a-zA-Z0-9_+.-]{1,32}$")
_DIR = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


class MountError(RuntimeError):
    pass


def _unescape(field: str) -> str:
    return _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), field)


def live_mounts(path: Path = Path("/proc/self/mountinfo")) -> list[dict]:
    """Load mountinfo, including spaces encoded as octal escapes."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise MountError(f"Cannot read Linux mount table: {exc}") from exc
    out = []
    for line in lines:
        left, delimiter, right = line.partition(" - ")
        before, after = left.split(), right.split()
        if not delimiter or len(before) < 6 or len(after) < 3:
            continue
        out.append({"mountpoint": _unescape(before[4]), "majmin": before[2], "mount_id": before[0],
                    "source": _unescape(after[1]), "fstype": after[0],
                    "options": before[5].split(",")})
    return out


def is_active(mountpoint: str, majmin: str | None = None) -> bool:
    for entry in live_mounts():
        if entry["mountpoint"] == mountpoint and (not majmin or entry["majmin"] == majmin):
            return True
    return False


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def active_managed() -> list[dict]:
    """Return current mounts, enriched with DriveBench history if known."""
    records = store.read_state().get("mounts", {})
    active = {m["mountpoint"]: m for m in live_mounts()}
    result = []
    for path, saved in sorted(records.items()):
        current = active.get(path)
        same = bool(current and current["majmin"] == saved.get("majmin")
                    and current["mount_id"] == saved.get("mount_id")
                    and boot_id() == saved.get("boot_id")
                    and not saved.get("unmounted_at"))
        result.append({**saved, "active": same,
                       "status": ("MOUNTED" if same else
                                  "PATH REUSED (not DriveBench's mount)" if current else "NOT MOUNTED"),
                       "observed": current})
    return result


def _sudo(args: list[str]) -> None:
    if not shutil.which("sudo"):
        raise MountError("sudo is missing. Install it with: sudo apt install sudo (from an administrative shell).")
    print("  Elevated command: sudo " + " ".join(args))
    try:
        # No shell, no password capture. sudo prompts through the controlling TTY.
        completed = subprocess.run(["sudo", "--", *args], check=False)
    except OSError as exc:
        raise MountError(f"Could not invoke sudo: {exc}") from exc
    if completed.returncode:
        raise MountError(f"Command failed (exit code {completed.returncode}). Check sudo access and messages above.")


def _root_owned_dir(path: Path) -> bool:
    try:
        data = path.lstat()
