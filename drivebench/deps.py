"""Explain missing Ubuntu dependencies without requiring Python pip packages."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

COMMANDS = [
    ("python3", "python3", True, "Python 3 runtime"),
    ("lsblk", "util-linux", True, "disk/partition inventory"),
    ("findmnt", "util-linux", False, "system-root detection"),
    ("udevadm", "udev", False, "USB settle and device identities"),
    ("lspci", "pciutils", False, "SCSI controller descriptions"),
    ("mount", "util-linux", True, "read-only partition mounting"),
    ("umount", "util-linux", True, "partition unmounting"),
    ("sudo", "sudo", True, "scoped privilege elevation for mount/unmount"),
    ("install", "coreutils", True, "safe creation of mount directories"),
    ("rmdir", "coreutils", True, "cleanup of empty mount directories"),
    ("blkid", "util-linux", False, "filesystem signature probing"),
    ("wipefs", "util-linux", False, "non-destructive signature inspection"),
    ("smartctl", "smartmontools", False, "drive SMART/SCSI health reporting"),
    ("sg_logs", "sg3-utils", False, "additional SCSI logs (future/advanced)"),
    ("sg_inq", "sg3-utils", False, "SCSI VPD serial-number inquiry"),
    ("wget", "wget", False, "GitHub ZIP download"),
    ("git", "git", False, "identify local checkout and manual upgrades"),
    ("dd", "coreutils", False, "zero/random overwrite with progress"),
    ("cmp", "diffutils", False, "full zero readback verification"),
    ("parted", "parted", False, "create a new partition table and partition"),
    ("partprobe", "parted", False, "inform kernel of updated partition table"),
    ("mkfs.exfat", "exfatprogs", False, "format for modern OS compatibility"),
    ("mkfs.fat", "dosfstools", False, "format FAT32 for older systems"),
]


def missing() -> list[tuple[str, str, bool, str]]:
    return [entry for entry in COMMANDS if not shutil.which(entry[0])]


def instructions(missing_commands: list[tuple[str, str, bool, str]] | None = None) -> str:
    entries = missing() if missing_commands is None else missing_commands
    if not entries:
        return "All listed Ubuntu commands are installed."
    packages = sorted({package for _cmd, package, _required, _desc in entries})
    lines = ["Missing Ubuntu components:"]
    for cmd, pkg, required, desc in entries:
        lines.append(f"  {cmd:<12} {desc} ({'required' if required else 'optional'}; package: {pkg})")
    lines.extend(["", "Install missing packages from an administrator terminal:",
                  "  sudo apt update", "  sudo apt install " + " ".join(packages)])
    if any(cmd == "sudo" for cmd, *_ in entries):
        lines.append("If 'sudo' is unavailable, ask a system administrator to install it using an administrative shell.")
    return "\n".join(lines)


def doctor() -> None:
    print("\nUBUNTU DEPENDENCY CHECK")
    for cmd, package, required, purpose in COMMANDS:
        where = shutil.which(cmd)
        print(f"  {cmd:<12} {'OK' if where else 'MISSING'}  [{package}] {purpose}")
    print("  sysfs:      " + ("OK" if Path('/sys/class/block').exists() else "MISSING (/sys/class/block)"))
    print("  user:       " + str(os.geteuid()) + " (must not be 0/root)")
    m = missing()
    if m:
        print("\n" + instructions(m))
    else:
        print("\nNo additional Python packages are required. No pip installation needed.")
    print("\nMounting will prompt through sudo for your password if required."
          " Mount/unmount, read-only diagnostics, wiping, formatting and report writing may use sudo only as selected.")
