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
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(data.st_mode) or data.st_uid != 0 or data.st_mode & 0o022:
        raise MountError(f"Unsafe mount directory {path}: must be root-owned, not a symlink, and not writable by group/others.")
    return True


def _ensure_dir(path: Path) -> None:
    if path != MOUNT_BASE and path.parent != MOUNT_BASE:
        raise MountError("Mount directory outside approved /mnt/drivebench root")
    if _root_owned_dir(path):
        return
    # Only fixed, whitelisted names are passed to an elevated command.
    _sudo(["install", "-d", "-o", "root", "-g", "root", "-m", "0755", "--", str(path)])
    if not _root_owned_dir(path):
        raise MountError("Mount directory is not owned safely by root after creation")


def _mount_options(fstype: str) -> list[str]:
    if not _FS.fullmatch(fstype):
        raise MountError(f"Unrecognized filesystem type {fstype!r}; no mount attempted")
    options = ["ro", "nosuid", "nodev", "noexec"]
    if fstype in {"ext3", "ext4"}:
        options.append("noload")
    if fstype == "xfs":
        options.append("norecovery")
    if fstype in {"vfat", "msdos", "exfat", "ntfs", "ntfs3"}:
        options.extend([f"uid={os.getuid()}", f"gid={os.getgid()}", "umask=022"])
    return options


def _block_majmin(part: dict) -> str:
    dev = str(part.get("path") or "")
    if not dev.startswith("/dev/"):
        raise MountError("Not a /dev block-device path")
    try:
        details = os.stat(dev)
    except OSError as exc:
        raise MountError(f"Device unavailable: {dev}: {exc}") from exc
    if not stat.S_ISBLK(details.st_mode):
        raise MountError(f"Refusing non-block device: {dev}")
    found = f"{os.major(details.st_rdev)}:{os.minor(details.st_rdev)}"
    expected = part.get("majmin")
    if not expected or found != expected:
        raise MountError(f"Device mapping changed since scan ({expected} -> {found}); refresh inventory")
    return found


def _allowed(disk: dict) -> None:
    blocked = [r for r in disk.get("reasons", []) if r.startswith("SYSTEM:") or r.startswith("IGNORED:")]
    if blocked:
        raise MountError("Disk is protected/excluded: " + "; ".join(blocked))


def _destination(disk: dict, part: dict) -> Path:
    stem = (disk.get("record_id") or "UNVERIFIED") + "-" + Path(str(part["path"])).name
    # Device metadata is untrusted; build a safe directory basename.
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", stem)[:70]
    if not _DIR.fullmatch(stem):
        raise MountError("Could not produce safe mount name")
    occupied = {m["mountpoint"] for m in live_mounts()}
    owned = store.read_state().get("mounts", {})
    for n in range(1, 10000):
        dest = MOUNT_BASE / f"{stem}-{n:03d}"
        if str(dest) not in occupied and str(dest) not in owned and not dest.exists() and not dest.is_symlink():
            return dest
    raise MountError("No unused mount directory available")


def mount_partition(disk: dict, part: dict, *, confirm: bool = True) -> str:
    _allowed(disk)
    if part.get("type") != "part":
        raise MountError("Select a real partition, not an LVM or other mapped device")
    fs = str(part.get("fstype") or "")
    if not fs:
        raise MountError("Filesystem unknown; refusing automatic mount. Image or inspect this partition first.")
    options = _mount_options(fs)
    majmin = _block_majmin(part)
    dev = str(part["path"])
    already = [m["mountpoint"] for m in live_mounts() if m["majmin"] == majmin]
    if already:
        raise MountError(f"Partition is already mounted at: {', '.join(already)}. No duplicate mount attempted.")
    target = _destination(disk, part)
    print(f"\nRead-only mount: {dev} ({fs}) -> {target}")
    print("Read-only is not forensic-grade write protection; image irreplaceable media first.")
    if confirm and input("Mount this partition read-only? Type YES: ").strip() != "YES":
        raise MountError("Cancelled")
    # Just before elevation: refresh existing devices and mount table.
    _block_majmin(part)
    if any(m["majmin"] == majmin for m in live_mounts()):
        raise MountError("Device became mounted in the meantime; refresh inventory")
    _ensure_dir(MOUNT_BASE)
    _ensure_dir(target)
    try:
        _sudo(["mount", "-t", fs, "-o", ",".join(options), "--", dev, str(target)])
        if not is_active(str(target), majmin):
            raise MountError("mount returned but exact device mount not found; inspect live mounts manually")
    except Exception:
        # Do not remove a directory while it may be mounted; leave it for inspection.
        raise
    current = next((m for m in live_mounts() if m["mountpoint"] == str(target) and m["majmin"] == majmin), None)
    if not current:
        raise MountError("Could not identify the new mount in the kernel table")
    try:
        store.record_mount(str(target), dev, majmin, disk.get("record_id"), disk.get("identity"), fs, current["mount_id"], boot_id())
    except Exception as exc:
        print("Could not save mount registry; attempting to undo mount.")
        try:
            _sudo(["umount", "--", str(target)])
        except MountError:
            raise MountError(f"Registry write failed; {target} MAY STILL BE MOUNTED. Check manually. Original error: {exc}") from exc
        raise MountError(f"Registry write failed; mount undone: {exc}") from exc
    return str(target)


def unmount_partition(mount_entry: dict, *, confirm: bool = True) -> None:
    dest = str(mount_entry["mountpoint"])
    if mount_entry.get("active") is not True:
        raise MountError("Not an active DriveBench-owned mount; no unmount attempted")
    path = Path(dest)
    if path.parent != MOUNT_BASE or not _DIR.fullmatch(path.name):
        raise MountError("Refusing mount directory outside DriveBench")
    _root_owned_dir(path)
    original_majmin = str(mount_entry["majmin"])
    if not is_active(dest, original_majmin):
        raise MountError("Mount/device changed before unmount; refresh list")
    print(f"Unmount {dest} (source {mount_entry['device']})")
    if confirm and input("Unmount? Type YES: ").strip() != "YES":
        raise MountError("Cancelled")
    if not is_active(dest, original_majmin):
        raise MountError("Mount/device changed before unmount; refresh list")
    _sudo(["umount", "--", dest])
    if any(m["mountpoint"] == dest for m in live_mounts()):
        raise MountError("Mount still present; directory not removed")
    store.record_unmount(dest)
    # Directory is root-owned; only remove the empty directory made by DriveBench.
    _sudo(["rmdir", "--", dest])
