
"""Check Ubuntu dependencies, including optional PDF reporting and printing."""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

# Each entry: executable, Ubuntu package, core requirement, purpose.
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
    ("testdisk", "testdisk", False, "read-only investigation of lost partitions"),
    ("photorec", "testdisk", False, "recover file contents to another drive"),
    ("ddrescue", "gddrescue", False, "create a disk image with resumable mapfile"),
    ("lp", "cups-client", False, "send optional PDF reports to a CUPS printer"),
    ("lpstat", "cups-client", False, "list configured CUPS printers"),
]

# Python modules are checked using THIS Python interpreter, not PATH.
# Each entry: import name, Ubuntu package, core requirement, purpose.
PYTHON_MODULES = [
    ("reportlab", "python3-reportlab", False, "generate PDF drive test reports"),
]


def _module_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def missing() -> list[tuple[str, str, bool, str]]:
    """Include optional modules in the same four-field format as commands."""
    commands = [entry for entry in COMMANDS if not shutil.which(entry[0])]
    modules = [("python:" + name, package, required, purpose)
               for name, package, required, purpose in PYTHON_MODULES
               if not _module_installed(name)]
    return commands + modules


def instructions(missing_commands: list[tuple[str, str, bool, str]] | None = None) -> str:
    entries = missing() if missing_commands is None else missing_commands
    if not entries:
        return "All listed Ubuntu components are installed."
    packages = sorted({package for _name, package, _required, _purpose in entries})
    lines = ["Missing Ubuntu components (optional items do not prevent DriveBench from running):"]
    for name, package, required, purpose in entries:
        status = "required" if required else "optional"
        lines.append(f"  {name:<18} {purpose} ({status}; package: {package})")
    lines.extend(["", "Install missing packages from an administrator terminal:",
                  "  sudo apt update", "  sudo apt install " + " ".join(packages)])
    if any(name == "sudo" for name, *_ in entries):
        lines.append("If sudo is unavailable, ask an administrator to install it from an administrative shell.")
    return "\n".join(lines)


def doctor() -> None:
    print("\nUBUNTU DEPENDENCY CHECK")
    for name, package, required, purpose in COMMANDS:
        available = shutil.which(name) is not None
        print(f"  {name:<18} {'OK' if available else 'MISSING'} "
              f"[{'required' if required else 'optional'}: {package}] {purpose}")
    for name, package, required, purpose in PYTHON_MODULES:
        available = _module_installed(name)
        print(f"  {'python:' + name:<18} {'OK' if available else 'MISSING'} "
              f"[{'required' if required else 'optional'}: {package}] {purpose}")
    print("  sysfs:              " + ("OK" if Path('/sys/class/block').exists()
                                       else "MISSING (/sys/class/block)"))
    print("  user:               " + str(os.geteuid()) + " (must not be 0/root)")
    problems = missing()
    if problems:
        print("\n" + instructions(problems))
    else:
        print("\nAll checked Ubuntu components are installed.")
    print("\nPDF reports and CUPS printing are optional. "
          "ReportLab is checked in the currently running Python environment.")
    print("Mounting may request sudo authentication. Drive operations elevate only when selected.")
