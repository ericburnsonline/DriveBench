# DriveBench

**DriveBench v0.2** is a Python-based, menu-driven Linux utility for discovering, inspecting, and managing storage drives, including legacy SCSI, IDE/PATA, SATA, and USB-connected drives.

Designed for Ubuntu Server, DriveBench provides a centralized interface for managing drives on a hardware testing and data recovery workstation.

The long-term goal is to provide a complete workflow for identifying, testing, recovering data from, wiping, formatting, and documenting used storage drives.

## Features

### Drive Discovery and Identification
- Automatically detect connected storage drives and partitions.
- Identify drive model, capacity, serial number, and connection type when available.
- Discover installed SCSI controllers and associated drives.
- Display SCSI host, channel, target, and LUN information.
- Refresh USB storage discovery after connecting or changing drives.

### Drive Protection and Exclusions
- Automatically protect system disks from destructive operations.
- Maintain a persistent list of excluded disks.
- Exclude specific SCSI controllers from processing.
- Update exclusions as hardware configurations change.
- Identify drives using persistent hardware identifiers when available rather than relying exclusively on Linux device names.

### Persistent Drive History
- Assign unique inventory IDs to recognized drives.
- Maintain records across application restarts and system reboots.
- Track when drives were first and last detected.
- Record operator notes and reported processing status.
- Distinguish previously recognized drives from newly discovered hardware when reliable identification is possible.

DriveBench does not automatically mark a drive as wiped simply because it was previously detected.

Some USB adapters do not expose reliable physical drive serial numbers. DriveBench handles these conservatively rather than incorrectly associating records with different drives.

### Partition Inspection and Mounting
- Display partitions and detected filesystems.
- Mount selected partitions read-only.
- Automatically create unique mount directories under `/mnt/drivebench/`.
- Track partitions mounted through DriveBench.
- Display active and previous mount records.
- Unmount DriveBench-managed partitions through the menu.
- Reconcile saved mount history with current Linux mount information after reboot.

Example mount directory:

```text
/mnt/drivebench/DB-000001-sdc1-001/
```

Each mounted partition receives its own directory, making it possible to examine and copy files using another terminal.

### Ubuntu Dependency Checking
- Check for required Linux utilities.
- Identify missing commands.
- Display appropriate Ubuntu installation commands.
- Operate without third-party Python packages.

## Current Limitations

Version 0.2 focuses on drive discovery, management, and read-only partition mounting.

The following functionality is planned but not yet implemented:

- SMART and SCSI diagnostic reporting.
- Drive surface testing.
- Configurable wiping using zeros or random data.
- Multiple wiping passes and verification.
- Filesystem formatting.
- Writing drive details and test results to a text file.
- Zebra label printing.
- Disk imaging and data recovery integration.

**Important:** DriveBench v0.2 does not provide disk wiping or formatting functionality.

## Requirements

- Ubuntu Linux (Ubuntu Server recommended).
- Python 3.
- Linux utilities: `lsblk`, `udevadm`, `lspci`, `mount`, and `umount`.
- `sudo` access for mounting and unmounting partitions.

No third-party Python libraries are required.

## Installation

### 1. Install Ubuntu Dependencies

```bash
sudo apt update
sudo apt install -y python3 util-linux udev pciutils sudo coreutils unzip
```

Many of these utilities are already installed on standard Ubuntu systems.

DriveBench also checks for missing dependencies when it starts.

### 2. Download DriveBench

Clone the repository:

```bash
git clone https://github.com/ericburnsonline/DriveBench.git
cd DriveBench
```

Alternatively, download the repository as a ZIP file through GitHub and extract it.

### 3. Launch DriveBench

```bash
./start-drivebench.sh
```

Alternatively:

```bash
python3 -m drivebench
```

Run DriveBench as your normal Linux user, **not as root**.

DriveBench invokes `sudo` for specific operations requiring elevated privileges, such as mounting and unmounting partitions.

### 4. Python Dependencies

DriveBench currently uses only Python's standard library.

No virtual environment or `pip install` is required.

## Main Menu

```text
DRIVEBENCH v0.2

 1. Scan disks and partitions
 2. Select drive (inspect / mount / history)
 3. List all mount points and mount status
 4. Unmount a DriveBench partition
 5. Refresh USB storage
 6. List SCSI controllers
 7. Manage disk/controller exclusions
 8. Drive history
 9. Check Ubuntu components / installation help
 0. Exit
```

All primary functions are accessed through the interactive menu.

## Mounting Partitions

1. Launch DriveBench.
2. Select **2. Select drive**.
3. Choose the desired drive.
4. Select **3. Mount a partition read-only**.
5. Choose the partition to mount.
6. Confirm the operation.

DriveBench creates a unique directory beneath:

```text
/mnt/drivebench/
```

For example:

```text
/mnt/drivebench/DB-000001-sdc1-001/
```

You can then access the files using another terminal:

```bash
ls -la /mnt/drivebench/DB-000001-sdc1-001/
```

Additional partitions receive separate directories.

To remove a mounted partition, return to the main menu and select **4. Unmount a DriveBench partition**.

### Mount Safety

DriveBench mounts partitions read-only by default.

For ext3/ext4 and XFS filesystems, additional mount options are used to avoid journal replay where supported.

Read-only mounting is not equivalent to hardware-level write protection. Particularly valuable or damaged disks should be imaged before attempting filesystem access.

Mounting partitions may require sudo privileges. DriveBench requests those privileges for the relevant operation rather than running the entire application as root.

## Persistent Configuration

DriveBench automatically creates a hidden configuration file:

```text
~/.config/drivebench/config.json
```

Persistent history and application state are stored at:

```text
~/.local/state/drivebench/state.json
```

These files preserve drive records and configuration across reboots.

DriveBench can store:
- Excluded disks.
- Excluded SCSI controllers.
- Drive identification records.
- Disk history and operator notes.
- Reported processing status.
- Mount history.

A drive without a trustworthy hardware identifier may not be automatically matched to an existing record after reconnection.

## USB and SCSI Behavior

### USB Storage

DriveBench provides a menu option to refresh USB storage discovery.

Linux normally discovers newly connected USB storage automatically. Depending on the adapter, disconnecting and reconnecting the USB interface may be necessary when replacing the attached IDE or SATA disk.

The USB refresh function does not forcibly reset USB controllers.

### SCSI Storage

DriveBench discovers installed SCSI controllers and connected drives.

Individual SCSI controllers can be excluded from DriveBench's inventory workflow.

This project assumes that legacy SCSI disks will be replaced only while the system is powered off. SCSI hot-swapping is not a supported workflow.

## Testing

Run the included automated tests from the repository directory:

```bash
python3 -m unittest discover -s tests -v
```

These tests validate core inventory, identity, protection, persistence, and mount-management logic without performing destructive disk operations.

Version 0.2 has passed its automated tests in a simulated Linux environment. Actual SCSI adapters, USB bridges, sudo policies, and filesystem behavior may vary.

## Roadmap

### Milestone 1: Drive Inventory
- Discover disks and partitions.
- Identify SCSI controllers.
- Configure disk and controller exclusions.
- Store persistent drive identification and history.

### Milestone 2: Partition Management
- Interactive menu interface.
- Read-only partition mounting.
- Automatic mount directories.
- Mount tracking and unmounting.
- Ubuntu dependency checks.

### Milestone 3: Drive Diagnostics
- SMART and SCSI health reports.
- Power-on hours and error counters.
- Surface read tests.
- Drive health history.

### Milestone 4: Drive Wiping
- Zero-fill and random-data wiping.
- Configurable number of passes.
- Progress reporting.
- Optional verification.
- Persistent wipe records.

### Milestone 5: Formatting and Documentation
- Filesystem selection and formatting.
- Text-based drive reports.
- Zebra label printing.
- Barcode-based drive identification.

### Future Enhancements
- Disk imaging and recovery workflows.
- Specialized vintage disk support.
- Advanced SSD sanitization.
- Batch drive processing.
- Expanded drive inventory and reporting.

## Safety

DriveBench interacts with physical storage devices and is intended for users familiar with Linux disk management.

Although version 0.2 does not include destructive disk operations, future versions will introduce wiping and formatting capabilities that can permanently destroy data.

Always verify the physical device identity before performing destructive operations.

**Never assume that Linux device names such as `/dev/sda` or `/dev/sdb` refer to the same physical disks after rebooting or reconnecting hardware.**

## License

DriveBench is released under the MIT License. See the `LICENSE` file for details.

Copyright (c) 2026 Eric Burns.
