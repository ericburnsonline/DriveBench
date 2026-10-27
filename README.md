# DriveBench v0.4.2: Ubuntu drive inspection, wiping, formatting, and reporting

DriveBench is a **terminal menu-based** utility for an Ubuntu storage-test bench with SCSI adapters, USB-to-IDE/SATA bridges, and normal SATA/IDE drives. **There are no CLI subcommands to learn.** Start it with `./start-drivebench.sh` (or `python3 -m drivebench` from the project directory). All functions are selected using numbered menus.

## Features

- Drives, partitions, bus types, serial numbers, SCSI host/target information, and SCSI/SAS/RAID controller discovery.
- Persistent disk and SCSI-controller exclusions; protective handling of system disks.
- Persistent drive records, with stable IDs across reboots when a trustworthy hardware serial or WWN is available. Records never automatically become “wiped” simply because a drive was seen again.
- **Read-only** partition mounting under `/mnt/drivebench/` with a **new uniquely numbered subdirectory for every mount** (such as `/mnt/drivebench/DB-000001-sdc1-001/`), plus an unmount menu.
- Tracks DriveBench-created mounts in local state; compares them with **live kernel mount information** whenever displaying mount status. Old mounts do not get treated as active after reboot.
- Lists mount points created outside DriveBench, but does not manage their unmounts.
- **Read-only filesystem probing** of whole disks and partitions using `blkid -p` and `wipefs --no-act`, including explicit cautions for unrecognized or ambiguous signatures.
- **Device health reports** via `smartctl` (ATA/SCSI/SAS/NVMe where supported) and optional `sg_logs` for older SCSI LOG SENSE counters. Unsupported attributes are shown as unavailable, never assumed healthy.
- **GitHub update checker:** list public branches, display exact commit identifiers, download a selected commit ZIP into `/tmp/drivebench-updates/` with `wget`, and compare downloaded files with installed files. **No code is overwritten automatically.**
- **SCSI duplicate-enumeration warning** when a drive model/capacity appears across eight or more target IDs on the same host/channel.
- Clear messages about missing Ubuntu commands, including exact `apt install` instructions. No Python package dependencies.
- Menu to log operator notes and a **clearly unverified, operator-reported** wipe performed outside the tool; DriveBench now supports supervised whole-disk wiping, FAT32/exFAT formatting, and on-disk reports.

- **Drive identification menu:** retry drive-native serial inquiry using `smartctl -i -j` or SCSI VPD (`sg_inq`), with an optional sudo retry for the selected disk. USB adapters are not trusted unless ATA pass-through exposes the drive's own serial.
- **Operator labels:** enter a manual serial and/or asset ID for each drive, stored immediately with atomic, fsynced writes to private persistent history. Manual data is never silently promoted to a verified hardware identity.

**Not included yet:** a dedicated *write/read* surface stress test, device-native SSD secure erase, Zebra label printing, and Macintosh HFS/Apple Partition Map creation. SMART/device-log inspection is included, but is not a surface test.

## Install on Ubuntu Server

In a terminal **logged in as your usual (non-root) user**, install system commands:

```bash
sudo apt update
sudo apt install -y python3 util-linux udev pciutils sudo coreutils
# Diagnostics, GitHub updates, wiping, formatting and verification:
sudo apt install -y smartmontools sg3-utils wget git parted dosfstools exfatprogs diffutils
# Optional new raw analysis, recovery, and imaging tools:
sudo apt install -y testdisk gddrescue
```

Ubuntu typically already includes almost all of these. DriveBench checks them every time it opens and provides the exact apt command for any that are missing. In the menu, choose **9. Check Ubuntu components / installation help** to repeat the check. `sudo` and `mount`/`umount` are required for the mount options, but inventory works without elevated privileges.

Download the archive and extract it to your home directory. For example, if the ZIP is in `~/Downloads`:

```bash
cd ~
unzip ~/Downloads/drivebench-menu-v0.4.2.zip
cd drivebench-menu-v0.4.2
./start-drivebench.sh
```

If `unzip` is missing, install it (`sudo apt install unzip`) or use Python's built-in ZIP extractor:

```bash
python3 -m zipfile -e ~/Downloads/drivebench-menu-v0.4.2.zip ~/
```

The archive has a top-level `drivebench-menu-v0.4.2` directory. **No virtual environment or pip install is needed.** `requirements.txt` lists no packages (there are no third-party Python dependencies).

Each time you want to run it:

```bash
cd ~/drivebench-menu-v0.4.2
./start-drivebench.sh
```

Run as your ordinary user, **not `sudo python3 -m drivebench`**. DriveBench refuses to launch as root. Its Python interface, scan, and history always run as the normal user. Only the selected mount/unmount/directory operations, read-only diagnostics, wipe, format, and report mount/unmount invoke `sudo`. Wipe and format each require two typed confirmations. Sudo may ask for your login password on the terminal, depending on your local sudo policy. On a headless Ubuntu Server, this is expected to work over an interactive SSH terminal if your account is authorized to run sudo. If your account lacks sudo access, ask the server administrator to grant the appropriate rights; discovery and the other menus remain available.

## Menus

```text
DRIVEBENCH v0.4.2
 1. Scan disks and partitions
 2. Select drive (inspect / mount / history)
 3. List all mount points and mount status
 4. Unmount a DriveBench partition
 5. Refresh USB storage
 6. List SCSI controllers
 7. Manage disk/controller exclusions
 8. Drive history
 9. Check Ubuntu components / installation help
10. Check GitHub branches / download update ZIP
 0. Exit
```

From **2. Select drive**, choose **9. Wipe / format / write drive report** for supervised preparation, **10. Identify drive / manually enter serial and asset ID** for physical identification, or **11. Raw disk analysis / evidence & recovery** for the new v0.4.2 tools.


### Raw disk evidence scanning and recovery (new in v0.4.2)

Choose **2. Select drive**, select your disk, then **11. Raw disk analysis / evidence & recovery**.

```text
RAW DISK ANALYSIS / RECOVERY
 1. Quick evidence scan (sampled read-only)
 2. Full evidence scan (entire disk read-only)
 3. Inspect/search partition structures with TestDisk
 4. Recover file contents to a DIFFERENT disk with PhotoRec
 5. Image to a DIFFERENT disk with GNU ddrescue
 6. View previous analysis results
 0. Back
```

**First, run a quick scan before any wiping or formatting.** It reads samples from the beginning, quarter points, and end of the physical disk. It checks for partition and legacy filesystem markers in the first region, plus common file headers throughout the sampled regions. A full scan then reads the complete accessible disk and counts recognizable signatures (JPEG, PNG, PDF, ZIP, GIF, ELF, SQLite, gzip, RAR) with offsets and a zero-byte ratio. The report does not automatically extract file contents. You may see false positives, embedded files, or fragmented data; **signature hits are *not* guaranteed complete or recoverable files.** High entropy does not demonstrate prior encryption or a successful wipe, and **absence of findings does not mean the drive was never used**.

The **TestDisk** choice can list the current partition layout or run a scripted `analyze,search` to investigate older/deleted partition structure. DriveBench deliberately omits TestDisk's `write` commands and does not expose its interactive partition-repair interface in this submenu. This is meant for evidence gathering, not altering partition tables.

The **PhotoRec** option performs file carving and writes recovered files into a new output directory on **another physical disk**. A PhotoRec run may produce a very large number of files, some containing sensitive previous-owner data. Handle and dispose of recovered contents responsibly. The optional **GNU ddrescue** feature saves a raw image (`.img`) and resumable progress map (`.map`) on another disk. Neither feature writes to the original drive. DriveBench checks that the selected destination is an existing writable directory on a verifiable local block device, then checks its ancestry so it cannot be on the source disk. It deliberately rejects unverifiable destinations such as overlay, network, and tmpfs mounts; it is still your responsibility to ensure enough free space. External recovery tools run through `sudo` and the outputs may be root-owned.

**DriveBench records** the time, drive identity, scan progress, signature counts, offsets, status, and outcomes in `~/.local/state/drivebench/state.json`. It does *not* store recovered personal file content inside its persistent records. Full scans checkpoint progress approximately every 256 MiB; after power loss the scan remains `IN_PROGRESS`, never falsely marked completed. For disks with a trustworthy hardware identity, history follows that disk across reboots. With a manually labeled disk, you must explicitly reattach its record after restarting. On completely unidentified/unattached drives, an unlinked event is saved but is not associated with an arbitrary later `/dev/sdX`.

Saved raw scan history is also included in a `DRIVEBENCH-REPORT.txt` subsequently generated on a DriveBench-formatted disk. Once a disk has been overwritten, those old file signatures may no longer be discoverable, making this pre-wipe capture valuable.

Dependencies: `dd` from `coreutils` for raw reads, `testdisk` (also provides `photorec`), `gddrescue` (provides `ddrescue`), `sudo`, `lsblk`, and `findmnt`. Select **9. Check Ubuntu components** on the main menu for install commands. No new Python packages are needed.

**Hardware testing limitation:** Read-only behavior, failure handling, and destination guards are covered by simulated tests; the raw scans and external recovery tools have **not** yet been tested on your actual SCSI controller. Start with the Seagate on a nonessential drive, verify the model/serial and active mount status, and run the quick scan first. Avoid repeated scans of a drive that is physically failing; make a ddrescue image first instead. Never use the wipe/format menu until you've finished any desired recovery.

### Serial-number identification and persistence (new in v0.4.1)

Choose **2. Select drive**, select the physical drive, then **10. Identify drive**:

1. Examine the drive-reported serial number and source, when available. DriveBench tries `lsblk`/udev first, followed by unprivileged `smartctl -i -j` and SCSI VPD inquiry for drives missing a reliable ID. When USB-to-IDE/SATA bridges hide a drive's native identity, the bridge's SCSI serial is deliberately *not* used for automatic history matching.
2. Choose **2. Retry automatic serial detection with sudo**, if the normal user cannot read drive inquiry data. This is a read-only inquiry. Smartmontools (`smartctl`) and sg3-utils (`sg_inq`) are checked in the dependency menu.
3. Choose **1. Enter / edit manual serial and asset ID** and confirm `SAVE`. Both values are saved separately from the hardware serial. Your values persist in `~/.local/state/drivebench/state.json` immediately, even if the application exits or the computer reboots. Do not use hardware serial fields to store guessed values.
4. With a reliable hardware identifier, history automatically follows the same disk across reconnects and reboots. **Without** one, DriveBench creates a persistent *manual-only* record, but never assumes the disk occupying a particular `/dev/sdX`, USB slot, or SCSI target is the same disk. After a restart choose **10 → 3. Reattach a previously saved manual record**, compare the disk's physical label, and type the saved asset/serial and `LINK` to associate its saved history with that disk for **that session only**. Capacity/model must also match.
5. Once manually attached, subsequent wipe, format, and report operations can be stored in that record, including interrupted or failed operations. Destructive commands also ask you to type the saved physical-label identifier. Reports include both serial sources and the asset ID with explicit provenance.

**Important limitation:** For anonymous disks, prior history can only be associated after a human validates the physical drive. An entered serial or matching slot is not proof. The tool cannot guarantee identity for physically identical media switched behind a bridge that does not support reliable device-generation tracking. Label such drives physically and confirm them before destructive actions.

DriveBench's status and configuration are saved separately from the program's installed source. Installing new versions should not overwrite the files under `~/.config/drivebench/` or `~/.local/state/drivebench/`. Back up those files periodically if the history matters.

### Wiping, formatting, and writing a drive report (new in v0.4)

**Destructive warning:** Wiping and formatting permanently destroy existing user data, including unknown or unsupported partition layouts. Complete your file recovery, cloning, or imaging first. Test this release with a sacrificial HDD before trusting it with your vintage equipment. No real physical disk was wiped or formatted during development.

From **2. Select drive**, choose **9. Wipe / format / write drive report**. These are separate, explicitly selected operations:

1. **Wipe entire physical HDD:** choose `zero` or `random`, 1–10 passes, and optional post-wipe readback. `dd` writes every exposed logical block with progress and flushes data before each pass is recorded complete. Zero readback checks that every accessible byte reads as zero using `cmp`; random readback checks readability only, **not exact random content**. Wipe starts are logged *before* writing. Failed, interrupted, or incomplete wipes are **never** marked complete.
2. **Partition and format:** choose FAT32 with **MBR** for older-system compatibility or exFAT with **GPT / Microsoft Basic Data** for modern Windows, macOS, and Linux. The menu suggests FAT32 for drives at or below 32 GiB (appropriate for many legacy SCSI disks), and exFAT for larger drives. FAT32 has a maximum file size just under 4 GiB; exFAT is not appropriate for many vintage systems. This action destroys the previous partition table even if the drive was not wiped first.
3. **Write `DRIVEBENCH-REPORT.txt`:** requires a completed DriveBench formatting record and exactly one detected FAT32/exFAT partition. DriveBench temporarily mounts that partition **read-write**, writes a new text report using exclusive creation (will not replace an existing report), syncs it, and unmounts. The report contains identification, capacity, dates, partition details, saved processing events, method/pass count and outcome, verification status, and diagnostic data captured **before the wipe** (SMART data and SCSI LOG SENSE results where supported). Save data to your Ubuntu workstation's persistent history too. The report option will refuse to write to an arbitrary preexisting filesystem.

Before every destructive stage, DriveBench scans the system again and checks the exact device name, kernel disk sequence (where available), size, serial/identity, SCSI address, excluded drives/controllers, read-only flag, live mounts, active swap, and storage stack holders. The normal user launches the program, and individual utilities run under `sudo` only after confirmation. **Do not hot-swap SCSI devices**, and do not replace a USB-connected disk behind its adapter during an operation. Drives detected as nonrotational/SSD or NVMe are refused by the v0.4 overwrite/format workflow, because generic overwriting is not equivalent to flash sanitization. USB bridges can misreport drive type; manually verify physical hardware before proceeding.

**Status persistence:** completed and incomplete processing operations are written to `~/.local/state/drivebench/state.json`. Drives with trustworthy serials/WWNs retain processing history across reboots. Operations on unidentifiable drives are logged as **unlinked events** and may be used for a report in the same running session, but they are **never automatically assigned to another disk** when a bridge or SCSI slot is reused. When available, Linux `diskseq` additionally detects reconnections during the running session. Prior status must not be treated as proof of sanitization for an unverified identity.

**Scope/assurance:** software overwrites cover normal addressable logical blocks, not necessarily reallocated physical sectors or hidden SSD flash. A completed overwrite is not an independent sanitization certificate. Verify the device's physical identity, and use an appropriate media-specific process for sensitive data.

### Mount one or more partitions

1. Start DriveBench and select **2. Select drive**.
2. Pick a drive by its displayed number.
3. Select **3. Mount a partition read-only**.
4. Select a detected partition, verify its device and filesystem, and type `YES` to approve.
5. DriveBench creates a new subdirectory under `/mnt/drivebench` and invokes `sudo mount` to attach it read-only.
6. From a **second terminal**, use the path shown, for example: `ls -la /mnt/drivebench/DB-000001-sdc1-001/` and copy needed files to another drive. The destination must be writable and have available space; source data is mounted read-only.
7. Repeat for other partitions. Each successful mount gets a new directory; already-mounted partitions cannot be mounted a second time from DriveBench.
8. Select **3. List all mount points and mount status** to see active and earlier DriveBench mounts and externally mounted partitions.
9. When finished copying, select **4. Unmount a DriveBench partition**; close any shells or programs using the partition first.

**Read-only safety details:** read-only mounting does *not* provide forensic-grade hardware write protection. To avoid journal replay on potentially dirty filesystems, DriveBench uses `ro,noload` for ext3/ext4 and `ro,norecovery` for XFS. For FAT, exFAT, and NTFS variants it assigns read permissions to the invoking user when supported by the filesystem. For other recognized filesystems it attempts the default kernel read-only mount and may be unable to read certain ownership-restricted files. Mounting older or damaged filesystems can fail or expose kernel bugs. For irreplaceable media, make a full disk image with a separate tool before attempting to mount it. See the Linux `mount(8)` manual.

**Missing filesystem support:** If an NTFS filesystem cannot mount, Ubuntu may require `sudo apt install ntfs-3g`. Modern kernels typically support exFAT; `exfatprogs` contains utilities for exFAT maintenance/formatting, not a general solution for missing kernel support. Older Macintosh HFS/HFS+ volumes and uncommon vintage SCSI disk formats may need specialist tools and sometimes cannot be mounted by generic Linux `mount` at all. DriveBench explains when a filesystem is unknown rather than guessing a mount command.

### Inspect filesystem signatures (introduced in v0.3)

From the **Select drive** menu, choose **6. Inspect filesystem / partition signatures**. This read-only process consults `blkid -p` (uncached low-level signatures) and `wipefs --no-act` (prints signature locations, **does not erase** anything) on the whole disk and its partitions. It can prompt to run the specific probe using `sudo`, if ordinary user permissions do not permit reading raw block devices.

**`"pttype": null` does not mean the disk is empty.** `PTTYPE` is the recognized *partition-table type* (`dos`, `gpt`, etc.), not the filesystem type. A null value means Linux has not identified a partition table using that lookup. A filesystem might be on the whole device (a "superfloppy" layout), the table may be corrupted, or the disk may use an unsupported vintage format. Even `blkid` and `wipefs` finding no signatures is **not** proof that valuable data is absent. If the disk might contain important files, image it before attempts at repair, formatting, or wiping. DriveBench does not automatically mount unknown filesystems.

### Drive health and SCSI logs (introduced in v0.3)

Under **Select drive**, choose **7. Show SMART / SCSI drive-health details** to call `smartctl --all --json`, then display supported fields such as model, serial, power-on hours, SMART status, SCSI grown defects and read/write errors, ATA reallocations/pending sectors, and NVMe media errors. Some USB bridges need SAT translation (`-d sat`), selectable in the menu. Raw-device access may require `sudo`.

Choose **8. Show detailed SCSI log pages** to run the read-only `sg_logs --all` report on older SCSI devices that expose additional LOG SENSE data. Attributes and vendor formats vary: **missing is not zero**, and even a passing SMART status does not prove media is error-free. Neither command starts a long/short drive self-test or performs a surface scan.

### GitHub updates and branches (introduced in v0.3)

Choose **10. Check GitHub branches / download update ZIP**. DriveBench queries GitHub's public branch list, displays the commit SHA for each branch, and offers a selected branch for download using **`wget`**. The ZIP is pinned to the exact commit, not an asynchronously moving branch head. The staged archive is under `/tmp/drivebench-updates/`; `/tmp` may be cleared after reboot.

When running from a real Git checkout, DriveBench compares local `HEAD` with the chosen remote SHA. For installs copied from a ZIP or uploaded manually, it explains that the local commit is unknown instead of claiming the system is current. A staged archive is compared against local installed files, listing **added**, **modified**, and **unchanged** files; the app never automatically replaces or deletes project files. Repeated downloads of the same branch commit reuse the staged ZIP if present.

For a cloned repository, the recommended manual upgrade, after reviewing local changes, is:

```bash
git status
git fetch origin
git switch main  # or choose a different existing branch
git pull --ff-only origin main
```

For a newly created remote branch, use `git switch --track origin/BRANCH` after fetching. With a ZIP/manual installation, inspect the staged ZIP, back up the app directory, and manually copy the new source files. Do not copy the top-level archive directory blindly onto your working directory. **The persistent config and history are outside the repository** (`~/.config/drivebench/` and `~/.local/state/drivebench/`) and must be retained. Never update a running process in place without restarting it afterwards.

The network updater requires a public GitHub repository and internet access. API/network errors are reported. If `wget` is absent, the program displays `sudo apt install wget`. It does not download or execute code silently.

### SCSI ID conflict troubleshooting (observed hardware issue)

On one Ubuntu test bench, a **Seagate ST318406LW** appeared around 15 times in the drive list, once at almost every SCSI target ID. `lsblk -d -o NAME,HCTL,SIZE,MODEL` showed the same 18 GB drive model at HCTL addresses `2:0:1:0` through `2:0:15:0`. It was an apparent **SCSI target-ID conflict** rather than a DriveBench display bug. After powering down and changing the drive's SCSI ID jumper, enumeration returned to normal.

An ID conflict between the drive and host adapter can cause this pattern. Most legacy SCSI controllers default to ID 7, but check the actual adapter configuration; never assume its ID. Before moving jumpers or cables, **power off completely**. Confirm every physical drive has a unique target ID and check termination/cabling if the problem persists. DriveBench now flags unusually repeated model/size combinations across 8+ target IDs as a **possible** conflict, not proof: genuinely identical physical drives can share a model and capacity.

Useful read-only troubleshooting command:

```bash
lsblk -d -o NAME,HCTL,SIZE,MODEL
```

### Permissions and directories

- Mount directory: **`/mnt/drivebench/`**, created as a root-owned directory not writable by other users. Each generated mount point is root-owned and named after the drive record ID and partition device name, with a numeric suffix.
- These directories are on your **Ubuntu test computer's existing filesystem**. The read-only mounting workflow does not create partitions; the separate explicitly destructive formatting menu does.
- Directory permissions are 0755. Some Linux/native UNIX partitions have restrictive permissions inside, so a normal user may still be unable to read certain files; DriveBench does not bypass filesystem permissions.
- Unmount uses `sudo umount` and removes the empty mount directory on success. Previously recorded mount events remain in history.
- DriveBench only offers unmount for mounts it registered and can still match against the live mount ID, partition major/minor, and system boot ID. After a power cycle, previous mounts are shown as not mounted; the tool will not try to unmount an old, stale entry.
- If power fails mid-mount before the state file is saved, the mount may exist without a managed record. Inspect the OS mount table and unmount such entries manually; DriveBench does not assume ownership of arbitrary existing mounts.

## First run and persistent drive status

The program automatically creates:

```text
~/.config/drivebench/config.json
~/.local/state/drivebench/state.json
```

These files persist across program exits, reboots and power cycling. They use the XDG config/state locations if configured in the environment and are created with restrictive permissions. The first time you run DriveBench, it lists disks, explains automatic system drive protection, and offers to configure disk/controller exclusions; later use menu **7** to edit the exclusions.

Disk history uses durable drive identities when available, never transient `/dev/sdX`. A drive may show `DISCOVERED`, `WIPED`, `WIPED_AND_FORMATTED`, `PREPARED`, `FORMATTED_NOT_WIPED`, `PROCESSING_INCOMPLETE`, `MANUAL_WIPE_REPORTED`, or `UNVERIFIED` depending on saved evidence and identity confidence. Marking an externally performed wipe requires choosing the per-drive history menu and explicitly confirming the report; that never claims DriveBench performed or verified the wipe. Reconnecting a disk can increase its seen count but never changes its processing state by itself.

**USB bridge caveat:** Some USB-to-IDE/SATA adapters report their *adapter's* serial number, not the drive's. DriveBench does not trust generic USB bridge serial numbers for history matching. Unidentified drives won't automatically inherit a past wipe status. Use a physical label and your own written notes for those until human-assisted drive matching is implemented. Exclusions can follow a disk's trustworthy hardware identity or the *physical port/slot* where an exclusion is appropriate. Slot exclusions also affect future drives inserted there.

**USB refresh:** menu **5** settles outstanding udev events and refreshes discovery. It does not forcibly reset USB hardware. If you swap an IDE/SATA drive behind an unchanged USB adapter, disconnect and reconnect the adapter safely to trigger kernel re-enumeration. For SCSI disks, the workflow assumes powering off before replacing the drive.

## Test without hardware

From the project directory:

```bash
python3 -m unittest discover -s tests -v
```

Automated tests use mocks and do not touch block devices. v0.4.2 adds raw analysis and destination-safety tests. v0.4.1 added coverage for automatic SCSI/SMART serial discovery, USB bridge rejection, user-supplied serial and asset persistence, manual-only reboot association, and interrupted-operation history. They cover identity/history, protected disks, mounts, SCSI duplicate-ID patterns, SMART summaries, GitHub branch staging, destructive preflight, confirmation cancellation, wipe progress and unsuccessful wipes. **The v0.4 destructive workflows have NOT been tested on a real disk.** Start with a clearly identified sacrificial conventional HDD; confirm safeguards and verify the resulting layout and report before processing anything valuable.

## For future development

Planned: advanced disk-image verification and recovery workflows, dedicated full-surface write/read testing, support for additional older filesystem layouts, Zebra label printing, and proper SSD-native sanitization. The current wipe, format, and report features are explicit, supervised operations and should be validated against real hardware before regular use.
