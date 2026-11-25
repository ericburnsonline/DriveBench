
# Standalone USB archive and recovery utility

`tools/usb_archive_recover.py` is a separate utility kept in the DriveBench
repository. It is **not** a DriveBench menu option, and its archives do not
automatically appear in DriveBench's drive history or seller PDFs.

It is designed for USB thumb drives, including running separate processes for
**different source drives** against one large backup volume. Start with
**archive-only mode**; do not use the erase mode until you have tested it on
expendable media and verified the saved files yourself.

## What it does

For a selected USB disk, the script:

1. Checks the source's identity and checks that the archive is on another,
   mounted, non-root, local filesystem; obtains cooperative locks.
2. Unmounts the source's mounted partitions (without force-unmounting busy ones).
3. Images the entire source disk with GNU ddrescue and checks its rescue map.
4. Calculates SHA-256 hashes of the original disk and the saved disk image.
5. Opens the image as a read-only loop device, copies existing files from its
   readable partitions, and verifies the copied files by SHA-256.
6. Runs PhotoRec against image partitions to attempt deleted-file carving.
7. Saves a `report.json` and recovery manifests in a distinct archive directory.
8. **Only in `--unattended` mode**, after the preceding checks succeed, writes
   zeros over the selected physical USB disk and attempts a full zero readback.

**Limits:** PhotoRec success does not prove that all deleted files were
recovered, or that the recovered contents are intact. A zero-file recovery can
still be reported as successful. SHA-256 confirms equality of the disk and
image *when they were read*; it does not replace independent backup inspection.
Zero overwrite is **not** a certified secure erase (especially on flash media).

## Prerequisites

From the DriveBench repository root on Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 gddrescue testdisk util-linux udev
```

`ddrescue` is provided by `gddrescue`, and `photorec` by `testdisk`.
The utility must run as root to access raw disks and use read-only mounts.
This is different from normal DriveBench, which must run as a regular user.

## Set up the archive destination

Use a **separate backup disk** with adequate free space, mounted before starting.
The default example assumes the backup partition is mounted at
`/mnt/drivebench-archive`, with archives inside its `usb-recovery` subfolder.
Do not format, mount over, or point this location at a source thumb drive.

After you have mounted the separate backup disk, verify the mount:

```bash
findmnt -T /mnt/drivebench-archive -o TARGET,SOURCE,FSTYPE
lsblk -o PATH,TYPE,TRAN,SIZE,MODEL,SERIAL,MOUNTPOINTS
```

The mount target must **not** be `/`, and the source must be the intended
backup partition. Only after confirming the mount, create the archive folder:

```bash
sudo install -d -m 0700 /mnt/drivebench-archive/usb-recovery
findmnt -T /mnt/drivebench-archive/usb-recovery -o TARGET,SOURCE,FSTYPE
```

The script also checks these conditions and refuses a root-filesystem archive
or a destination on the selected USB source. It needs approximately **twice
source capacity plus 256 MiB** free as a minimum; file carving may consume
much more. Ensure plenty of additional space when running parallel jobs.

## Install the configuration

The committed file `tools/usb_archive.ini.example` is a **template** stored
next to the script. Copy it once, removing `.example`, and edit only the copy:

```bash
cp tools/usb_archive.ini.example tools/usb_archive.ini
chmod 600 tools/usb_archive.ini
nano tools/usb_archive.ini
```

The script finds the local INI using its own file location, regardless of your
working directory or whether you launch it with `sudo`. The exact
`/tools/usb_archive.ini` path is in `.gitignore`, so normal Git operations
will not add your machine-specific settings. **Do not force-add that file.**
Keep `tools/usb_archive.ini.example` unchanged in the repository.

Change `destination` to your actual mounted backup directory. `recovery_mode`
can be `auto` (recommended), `freespace`, or `wholespace`:

- `auto` uses a free-space PhotoRec scan for recognized supported filesystems;
  otherwise it scans the whole partition.
- `freespace` requires a supported filesystem or the job stops.
- `wholespace` attempts carving across the entire partition.

**The local configuration file is required even when `--output` is provided.**
`--output` overrides only the configured destination for that invocation.
You can supply a different INI with `--config /absolute/path/to/file.ini`.

## Run in archive-only mode (recommended)

From the repository root:

```bash
sudo python3 tools/usb_archive_recover.py
```

Select a displayed whole USB disk (example: `/dev/sdb`), then type the exact
confirmation requested, such as `ARCHIVE /dev/sdb`. The script does **not**
zero the drive in this mode. It may unmount source partitions to image them.
Never assume a `/dev/sdX` letter identifies the same drive after reconnection.

Other non-erasing examples:

```bash
sudo python3 tools/usb_archive_recover.py --recovery-mode wholespace
sudo python3 tools/usb_archive_recover.py --output /mnt/other-backup/usb-recovery
```

The override destination must also be an **existing directory on a separate,
mounted, non-root, local filesystem**, and the INI must still exist.

## Destructive option: automatic zero overwrite

```bash
sudo python3 tools/usb_archive_recover.py --unattended
```

**WARNING: THIS MODE ERASES THE SELECTED USB DISK.** The name is somewhat
misleading: you still choose a drive interactively and must type an exact
phrase such as `WIPE /dev/sdb`. This single phrase is authorization for the
script to begin zero overwrite **without another prompt** once its archive
checks succeed. Never use this option for a source that contains needed data
unless the archive has been independently reviewed and you accept the risk.
If interrupted during the wipe, the disk can be **partially erased**.

A completed wipe requires a full zero readback; failures and incomplete
readback must not be represented as verified erasure. The log/report are
not a secure-erase certificate.

## Parallel terminals

Run a separate instance for each **different USB source drive**. You can
archive to the same mounted backup filesystem in parallel. The script uses
advisory cross-process locks for physical source disks, shared destination
disks, and loop-device allocation. It will reject conflicting selections.
These locks apply **only to cooperating copies of this script**; unrelated
Linux utilities do not honor them. Avoid running other disk operations on the
same devices. Parallel jobs compete for USB bandwidth and backup-disk I/O.

Each selected disk receives a separate directory resembling:

```text
/mnt/drivebench-archive/usb-recovery/
  YYYYMMDD_HHMMSS_microseconds_sdX_SERIAL/
    drive.img
    ddrescue.map
    report.json
    partition_01/
      existing_files/
      deleted_recovered/
      recovered_files.sha256
      photorec_output.txt
    partition_02/
      ...
```

Actual partition directories vary. Review `report.json` for
`image_verified`, `archive_verified`, and
`wipe_complete_and_readback_verified`. A `false` value is not proof that a
later phase completed; check the report and command output before reuse.
Archives and recovered files may contain private information: protect the
backup volume and never commit archive output to GitHub.

## Troubleshooting and testing

- **Configuration file not found:** copy `tools/usb_archive.ini.example`
  to `tools/usb_archive.ini` and edit `destination`, as shown above.
  `--output` by itself does not bypass this requirement.
- **Destination is root or unmounted:** mount the backup partition, check
  `findmnt -T`, and confirm the configured subdirectory exists on that mount.
- **Not enough space:** free capacity on the backup disk; PhotoRec results
  can require much more room than the raw image.
- **No USB disks shown:** inspect `lsblk` and the USB connection; the utility
  lists only disks whose Linux transport is reported as `usb`.
- **Source busy or already locked:** stop other operations and confirm no
  other copy of the script is using that source. Never force lock removal.
- **Image, mount, or PhotoRec failure:** the job stops; check command output
  and `report.json`. It does not intentionally initiate a wipe before the
  archive stages succeed.

Show command-line help without accessing disks:

```bash
python3 tools/usb_archive_recover.py --help
```

Run the project tests from the repository root:

```bash
bash run-tests.sh
```

`tests/test_usb_archive_parallel.py` uses simulated devices. Passing it does
**not** validate a real USB archive or destructive zero-overwrite operation.
Start with an expendable thumb drive in archive-only mode and inspect its
saved image, copied files, and recovery results before trusting the tool.
