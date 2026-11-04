
# DriveBench: Ubuntu drive inspection, recovery, and reporting

DriveBench is a **terminal menu-based** utility for an Ubuntu storage-test bench with SCSI adapters, USB-to-IDE/SATA bridges, and normal SATA/IDE drives.

**There are no CLI subcommands to learn.** Start it with `bash start-drivebench.sh` (or `python3 -m drivebench` from the project directory). All functions are selected using numbered menus.

## Features

- Discover disks, partitions, bus types, serial numbers, SCSI host/target information, and SCSI/SAS/RAID controllers.
- Persistent disk and controller exclusions, with automatic protection of system disks.
- Persistent drive records with stable IDs across reboots when trustworthy hardware identities are available.
- Read-only partition mounting under `/mnt/drivebench/`, using uniquely numbered mount directories.
- Track mounts using saved history and live kernel mount information.
- Read-only filesystem signature inspection using `blkid` and `wipefs --no-act`.
- SMART and SCSI health reporting, including power-on counters, error information, and device-supported diagnostic data.
- GitHub branch discovery, version comparison, and staged ZIP downloads without automatic source replacement.
- Detect possible duplicate SCSI target-ID enumeration.
- Identify drives using hardware-reported serials, operator-supplied serials, and asset IDs, with appropriate provenance.
- Supervised whole-disk overwriting, optional readback verification, partitioning, formatting, and on-disk text reports.
- Read-only raw disk evidence scanning, including recognizable file signatures and filesystem structures.
- Full-surface uniform-byte verification for patterns such as `0x00`, `0xFF`, `0x6C`, or any other byte.
- Persistent records of completed, failed, and interrupted analysis operations.
- Optional TestDisk, PhotoRec, and GNU ddrescue integration for investigation and recovery.
- **Letter-size PDF test reports** generated from saved drive identification and completed diagnostics.
- PDF regeneration from saved history, including after the drive has been disconnected.
- Optional CUPS printing of letter-size reports through configured Ubuntu printers.
- **Antistatic bag-label PDFs** in 2.25 × 4-inch and 4 × 6-inch formats.
- Bag labels containing drive identification, serial number, capacity, interface, and available completed test summaries.
- Label regeneration from saved drive history, without requiring the physical drive to remain connected.
- DriveBench version and GitHub project URL included in printed report and label layouts.

**Not included yet:** direct Zebra/ZPL printing, a dedicated write/read surface stress test, device-native SSD secure erase, and Macintosh HFS/Apple Partition Map creation. SMART inspection alone is not a surface test.

## Install on Ubuntu Server

Log in as your normal, non-root user.

Install required system components:

```bash
sudo apt update
sudo apt install -y python3 util-linux udev pciutils sudo coreutils
```

Additional diagnostics, preparation, and GitHub utilities:

```bash
sudo apt install -y smartmontools sg3-utils wget git parted dosfstools exfatprogs diffutils
```

Optional recovery and imaging tools:

```bash
sudo apt install -y testdisk gddrescue
```

Optional PDF generation and printing support:

```bash
sudo apt install -y python3-reportlab cups-client
```

Both the letter-size reports and bag-label PDFs require **ReportLab**.

Verify the Python dependency:

```bash
python3 -c "import reportlab; print(reportlab.Version)"
```

If using a Python virtual environment, install ReportLab into that environment rather than assuming Ubuntu's system package is available.

For direct printing through Ubuntu, a working CUPS server and configured printer queue are also required. PDF generation does not require CUPS.

### Download and launch

Download the latest source archive from:

https://github.com/ericburnsonline/DriveBench

Choose **Code → Download ZIP**, extract it into a new directory, and enter that directory.

Run:

```bash
bash run-tests.sh
bash start-drivebench.sh
```

Alternatively, clone the repository:

```bash
git clone https://github.com/ericburnsonline/DriveBench.git
cd DriveBench
bash run-tests.sh
bash start-drivebench.sh
```

To test an unmerged feature branch, select that branch before downloading its ZIP or switch branches using Git.

**Do not launch DriveBench as root.**

Run it as your normal user. Individual device operations request elevated privileges only when needed.

## Main menu

```text
DR I V E B E N C H  v0.4.5  |  Ubuntu drive workstation

 1. Scan disks and partitions
 2. Select drive (inspect / mount / history)
 3. List all mount points and mount status
 4. Unmount a DriveBench partition
 5. Refresh USB storage
 6. List SCSI controllers
 7. Manage disk/controller exclusions
 8. Drive history
 9. Check Ubuntu components / installation help
10. Check GitHub for branch updates / download ZIP
 0. Exit
```

After choosing a drive, the additional options include:

```text
 9. Wipe / format / write drive report (DESTRUCTIVE)
10. Identify drive / manually enter serial and asset ID
11. Raw disk analysis / evidence & recovery
12. Generate / print PDF drive report (non-destructive)
13. Generate antistatic bag label PDF (non-destructive)
```

## Printable letter-size PDF drive reports (v0.4.4)

DriveBench can generate a seller-facing PDF containing recorded drive identification and completed diagnostic results.

The PDF is generated on the Ubuntu workstation, not written onto the drive under test.

**You do not need to wipe, partition, or format a drive to generate a PDF report.**

### Generate a full-page PDF

1. Launch DriveBench normally.
2. Choose **2. Select drive**.
3. Select the physical drive and verify its model and serial number.
4. Choose **12. Generate / print PDF drive report**.
5. Choose **1. Generate / regenerate PDF from saved drive results**.
6. Optionally collect current SMART/SCSI information.
7. DriveBench saves the PDF and displays its location.

If a report already exists, type `REGENERATE` to replace it.

Otherwise, the existing report is preserved.

### Report location

Full-page reports are saved under:

```text
~/DriveBench-Reports/
```

A sample filename:

```text
SEAGATE-spi-ST318406LW-18GB-SN-3FE1WTKP00007310BJH2.pdf
```

The filename includes:

- Manufacturer
- Interface or transport
- Model
- Decimal capacity
- Hardware-reported serial number, or an explicitly identified alternative

Filenames are sanitized to avoid unsafe filesystem characters.

A regenerated report normally uses the same filename when its identifying information is unchanged.

### Report contents

Reports can include:

- Manufacturer and model
- Interface and capacity
- Hardware serial number
- Operator-supplied serial number, separately identified
- DriveBench inventory ID
- Latest completed full raw-disk scan
- Full logical-surface verification
- Uniform-byte analysis
- Explicitly collected SMART information, when supported

Missing test results are not represented as successful tests.

The report does not include recovered filenames, personal file contents, or complete internal recovery history.

The footer includes the DriveBench version and project URL:

https://github.com/ericburnsonline/DriveBench

Reports are designed to accept additional diagnostic sections as DriveBench evolves.

### Regenerate after disconnecting a drive

Choose:

**Main menu → 8. Drive history → select record → 4. Generate / regenerate PDF**

This uses previously saved information and does not require the original drive to remain connected.

Current SMART data collected only for an earlier PDF is not necessarily retained in persistent history. Regenerating from history cannot reconstruct readings that were never saved.

### Printing a full-page report from Ubuntu

The report-printing option uses the Ubuntu CUPS printing system.

After configuring a printer, choose:

**Select drive → 12 → 2. Print saved PDF**

Select the desired CUPS printer and type `PRINT`.

The initial implementation requests:

- US Letter paper
- Single-sided printing
- One copy

A print job accepted by CUPS is not proof that it physically printed successfully.

**Hardware printing status:** A DriveBench-generated Seagate ST318406LW report was successfully printed from Windows after copying the PDF from Ubuntu. Direct Ubuntu printing experienced CUPS/USB printer problems and remains a separate troubleshooting task. The PDF itself was confirmed to be a valid one-page, letter-size document.

## Antistatic bag-label PDFs (v0.4.5)

DriveBench can generate small identification labels for placement on the **outside of antistatic drive bags**.

These labels complement the detailed full-page report.

They are intended for inventory identification and concise reporting, not as replacements for the manufacturer's original drive label.

### Supported label sizes

| Label size | Intended use |
| --- | --- |
| **2.25 × 4 inches** | Preferred compact drive-bag label |
| **4 × 6 inches** | Larger label with additional available space |

The default is **2.25 × 4 inches**.

Dimensions are specified as width across the label and length in the feed direction.

The generated PDF uses the exact selected page dimensions.

### Generate a label with the drive connected

1. Start DriveBench.
2. Choose **2. Select drive**.
3. Select and verify the physical drive.
4. Choose **13. Generate antistatic bag label PDF**.
5. Select **2.25x4** or **4x6**.
6. DriveBench displays the destination filename.
7. The label PDF is generated without writing to the drive or sending a printer job.

If the label already exists, type `REGENERATE` when prompted to replace it.

### Generate a label from saved history

The original drive does not have to remain connected.

Choose:

**Main menu → 8. Drive history → select drive record → 5. Generate / regenerate an antistatic bag label PDF**

This uses saved identification and completed scan records.

For drives without a trustworthy hardware identity, select the appropriate saved record carefully and confirm the physical drive's identification before attaching the label.

### Label contents

Depending on available data, labels contain:

- DriveBench heading
- Manufacturer
- Model
- Decimal capacity
- Interface, including a human-readable SCSI designation for `SPI`
- Hardware-reported serial number, if available
- Operator-supplied serial number or asset ID, labeled appropriately
- DriveBench inventory ID
- Short full-surface scan summary
- Verified uniform-byte pattern, when supported by a completed full scan
- Reference to the enclosed detailed test report
- DriveBench GitHub URL and version
- Statement that the label is not a sanitization certificate

The entire serial number is retained rather than silently truncated.

If a required text field cannot fit at the minimum supported font size, label creation returns an error instead of generating an incorrectly shortened identifier.

### Scan-result safeguards

The label generator uses the latest **completed full scan**.

An incomplete or failed scan cannot replace a previous completed result.

A full logical-surface read is described as complete only when the recorded scan confirms full coverage of the reported disk capacity.

The label reports a uniform byte as verified only when the saved analysis contains `VERIFIED_FULL_SURFACE` and the full-read requirements are satisfied.

Otherwise, the label does not claim that the entire disk has been verified.

Even a verified uniform pattern does not establish whether a drive is factory-new, previously used, securely erased, or sanitized in inaccessible physical sectors.

### Label file location

Bag labels are saved under:

```text
~/DriveBench-Reports/labels/
```

Example:

```text
SEAGATE-spi-ST318406LW-18GB-SN-3FE1WTKP00007310BJH2-bag-label-2p25x4.pdf
```

The larger alternative uses a different suffix:

```text
-bag-label-4x6.pdf
```

The size is included in the filename so different label formats do not overwrite one another.

Regeneration requires explicit confirmation before replacing an existing PDF.

Generated labels are stored locally on the Ubuntu workstation with restricted file permissions.

### Printing the label

**Version 0.4.5 generates PDF labels but does not automatically send them to the Zebra printer.**

Initially, the PDF can be copied to a Windows computer and printed using the Zebra driver already configured there.

When printing:

1. Select the Zebra printer with the matching label stock.
2. Select the same paper dimensions as the generated PDF.
3. Print at **Actual size / 100%**.
4. Disable **Fit to page**, automatic scaling, and unintended margins.
5. Check the printed label's orientation and alignment before printing additional copies.

Do not use DriveBench's existing letter-size CUPS print option for the small label. That function currently requests Letter media.

Future versions may support direct Zebra printing through appropriately configured CUPS media settings or native ZPL commands.

### Packaging recommendations

Place the tested drive in a suitable antistatic bag.

Apply the identification label to the **outside of the bag**, leaving the manufacturer's drive label unobstructed.

Seal the bag using an appropriate ESD warning sticker or equivalent closure.

Include the full-page test report separately.

For a drive being sold without a new filesystem, there is no need to format it merely to place a DriveBench report on the media.

## Raw disk evidence scanning and recovery (v0.4.2)

Choose:

**2. Select drive → select disk → 11. Raw disk analysis / evidence & recovery**

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

The quick scan reads selected disk regions and searches for recognizable signatures.

The full scan reads the accessible logical disk surface, tracking signature evidence and scan completeness.

Recognized file headers may include JPEG, PNG, PDF, ZIP, GIF, ELF, SQLite, gzip, and RAR.

File signatures are only candidates. They do not establish that a complete file exists or can be recovered.

Finding no signatures does not prove that a drive was never used.

### Uniform-byte verification (v0.4.3)

Uniform-byte analysis is integrated into quick and full scans.

It detects whether all examined bytes have a single value, such as:

- `0x00`
- `0xFF`
- `0x6C`
- Any other byte value

The scanner compares every byte returned by its read operation.

| Result | Meaning |
| --- | --- |
| `VERIFIED_FULL_SURFACE` | Completed full logical-surface scan with one uniform byte |
| `SAMPLED_OR_PARTIAL_ONLY` | Examined regions match, but full coverage is not established |
| `NOT_UNIFORM` | Different byte values were detected |
| `NO_DATA` | No bytes were examined |

The distinction between full-surface verification and sampled observations is important.

**Real hardware example:** In October 2026, DriveBench completed a read-only scan of 18,351,959,040 bytes on a Seagate ST318406LW and verified a uniform `0x6C` pattern across the complete host-readable logical surface.

This does not establish the origin of the pattern or prove the drive is factory-new.

It also does not constitute certified secure sanitization.

### Recovery and imaging

The TestDisk integration investigates partition structures without intentionally modifying partition tables.

PhotoRec can recover file contents to another physical disk.

GNU ddrescue can create a disk image and resumable mapfile on another disk.

Recovery destinations must be verified as separate from the source disk.

DriveBench does not store recovered personal file contents inside its persistent scan records.

These external recovery workflows require further real-hardware validation before routine use.

### Persistent analysis history

Raw analysis events are saved in:

```text
~/.local/state/drivebench/state.json
```

Records can include:

- Drive identity
- Analysis type
- Start and completion times
- Scan coverage and progress
- Recognizable signatures
- Uniform-byte verification results
- Completed, interrupted, and failed statuses

Incomplete scans are not marked as successfully completed.

For drives with trustworthy hardware identity, history can follow the drive across restarts.

Unidentified drives require explicit operator confirmation before reconnecting saved manual records.

## Drive identification and serial persistence (v0.4.1)

Choose:

**2. Select drive → select disk → 10. Identify drive**

DriveBench can obtain available drive-native serial information using SMART and SCSI inquiry methods.

USB bridge serial numbers are not automatically trusted as the drive's actual serial number.

The menu allows:

1. Entering or editing an operator-supplied serial number and asset ID.
2. Retrying native serial detection with sudo.
3. Reattaching a saved manual record after restart when automatic identity is unavailable.

Operator-supplied values are saved separately from hardware-reported identifiers.

A matching disk model, size, USB slot, or Linux device path is not proof that two devices are physically identical.

Manual-only records therefore require explicit confirmation before being associated with a drive after reboot.

This distinction is especially important before destructive operations.

## Wiping, formatting, and on-drive reports (v0.4)

**WARNING:** Wiping and formatting destroy existing data.

Always complete any necessary copying, imaging, or recovery first.

The destructive workflow has been tested using mocked operations but still requires validation on a sacrificial physical HDD.

From **2. Select drive**, choose:

**9. Wipe / format / write drive report**

### Whole-disk overwrite

DriveBench supports supervised overwrites using zero or random data, with 1–10 passes.

Optional verification includes:

- Full zero readback verification after zero overwrite.
- Readability checking after random overwrite.

Random readback does not independently prove an exact byte-for-byte match with the generated random stream.

Failed or interrupted operations are not recorded as successful wipes.

### Partitioning and formatting

Supported options include:

- MBR/FAT32 for older-system compatibility.
- GPT/exFAT for newer Windows, macOS, and Linux systems.

The user must explicitly select and confirm destructive operations.

Formatting is separate from wiping.

### On-drive text report

DriveBench can write `DRIVEBENCH-REPORT.txt` to an appropriately formatted partition created through its preparation workflow.

Unlike the printable PDFs, this operation writes data directly onto the target disk.

It is unnecessary when the goal is to sell the drive unformatted with a separate printed report.

### Destructive-operation safeguards

DriveBench performs identity and preflight checks before destructive actions, including:

- System-disk protection
- Current device path and available disk sequence information
- Model, capacity, and identity checks
- Existing mounts and storage dependencies
- Controller and drive exclusions
- Device read-only status
- Detection of unsupported solid-state targets

Never hot-swap SCSI devices during operation.

Software overwrites do not necessarily address hidden or reallocated physical sectors.

A completed overwrite is not an independent sanitization certificate.

## Mount partitions read-only

1. Launch DriveBench.
2. Choose **2. Select drive**.
3. Select the desired physical drive.
4. Choose **3. Mount a partition read-only**.
5. Select the partition and confirm.
6. Use the displayed mount path from another terminal.
7. Unmount the partition using the main menu when finished.

Example mount path:

```text
/mnt/drivebench/DB-000001-sdc1-001/
```

DriveBench tracks managed mounts and checks live kernel mount information.

Read-only mounting does not provide forensic-grade hardware write protection.

Ext3/ext4 and XFS mounts use additional options intended to avoid journal replay where applicable.

Damaged or unusual filesystems may require specialist tools.

Older Macintosh formats and uncommon vintage SCSI layouts may not mount using standard Linux filesystem drivers.

If data is irreplaceable, image the disk before experimenting with mounts.

## Filesystem signatures

Choose **6. Inspect filesystem / partition signatures** from the selected drive's menu.

DriveBench uses read-only probes such as:

```bash
blkid -p
wipefs --no-act
```

A missing partition-table signature does not prove a disk is blank.

For example, `PTTYPE=null` means a recognized partition-table type was not returned by that probe. It does not rule out whole-device filesystems, unsupported layouts, or previously stored data.

## SMART and SCSI health reporting

Choose:

- **7. Show SMART / SCSI drive-health details**
- **8. Show detailed SCSI log pages**

Supported information can include:

- Drive model and serial
- Device-reported SMART status
- Power-on hours
- Start/stop counts
- Read/write error counters
- Corrected and uncorrected errors
- SCSI grown defect counts
- Additional vendor-supported SCSI log information

Not all devices provide the same attributes.

A missing value must not be interpreted as zero.

For some older SCSI drives, GLTSD (Global Logging Target Save Disabled) can make historical counters incomplete across power cycles.

Low reported power-on hours do not establish factory-new condition.

A passing SMART result does not guarantee that every sector is healthy.

## GitHub update checking

Choose **10. Check GitHub branches / download update ZIP** from the main menu.

DriveBench can:

- List available repository branches.
- Display commit identifiers.
- Download a selected commit archive.
- Compare downloaded files with installed files.
- Stage an update without automatically replacing running code.

This feature does not install updates automatically.

For a Git checkout:

```bash
git status
git fetch origin
git switch main
git pull --ff-only origin main
```

To test a feature branch, download or check out that branch separately.

Saved configuration and drive history are outside the source directory and should not be overwritten by an application update.

## SCSI ID conflict troubleshooting

During testing, a Seagate ST318406LW appeared repeatedly at multiple SCSI target IDs because of a hardware configuration conflict.

After powering down and correcting the drive's SCSI ID jumper, enumeration returned to normal.

Useful inspection command:

```bash
lsblk -d -o NAME,HCTL,SIZE,MODEL
```

When troubleshooting:

- Power off before changing SCSI cables, jumpers, or devices.
- Ensure devices on the same bus have unique SCSI IDs.
- Check controller ID configuration.
- Verify termination and cabling.
- Do not assume duplicate model listings always indicate a conflict.

DriveBench can warn about unusually repeated model/capacity combinations across SCSI IDs.

## Configuration, history, and permissions

Default persistent files:

```text
~/.config/drivebench/config.json
~/.local/state/drivebench/state.json
```

These survive ordinary application upgrades and reboots.

DriveBench uses the XDG configuration and state locations when configured.

Typical statuses include:

- `DISCOVERED`
- `UNVERIFIED`
- `PROCESSING_INCOMPLETE`
- `WIPED`
- `FORMATTED_NOT_WIPED`
- `WIPED_AND_FORMATTED`
- `PREPARED`
- `MANUAL_WIPE_REPORTED`

Externally reported wipes are not represented as verified DriveBench operations.

The normal application user must not be root.

Filesystem mounting and other privileged drive operations elevate only when necessary.

PDF generation and label generation run as the ordinary user and do not require writing to the attached drive.

## Testing

From the repository root:

```bash
bash run-tests.sh
```

Or:

```bash
python3 -m unittest discover -s tests -v
```

Automated tests are designed to use mocked hardware operations rather than performing destructive actions on physical drives.

Previous releases include coverage for:

- Drive discovery and stable identification
- Persistent history
- SCSI duplicate-enumeration warnings
- Mount protection
- Destructive-operation preflight safeguards
- GitHub update staging
- SMART information handling
- Raw disk scanning and uniform-byte verification
- PDF report generation and regeneration
- CUPS print-job submission

Version 0.4.5 adds label-related tests covering:

- Both supported PDF page sizes
- Hardware and operator-supplied identifiers
- Completed versus incomplete scan results
- Uniform-byte verification safeguards
- Deterministic label filenames
- Existing-file regeneration protection
- History-based label generation
- CLI menu integration
- Avoidance of automatic printer submission

The expanded v0.4.5 suite must pass on Ubuntu before merging this feature branch.

Generated label PDFs must also be inspected and physically printed at their intended dimensions before the layout is considered hardware-validated.

**Destructive wipe and format workflows still require testing on a sacrificial physical HDD.**

## Future development

Planned improvements include:

- Direct Zebra label printing using native printer commands or correctly configured CUPS media settings.
- Smart File Discovery & Recovery for mounted Windows, macOS, and Linux filesystems.
- Configurable file-discovery profiles for documents, photographs, videos, backups, and other file types.
- Selective file copying to a separate disk with verification and persistent recovery history.
- Dedicated full-surface write/read stress testing.
- Additional vintage partition and filesystem formats.
- Macintosh SE drive cloning and preservation workflows.
- Improved stored SMART history for regenerated reports.
- Batch report and label generation for multiple drives.
- Additional disk-image verification tools.
- Media-specific SSD sanitization workflows.

The development priority is to improve DriveBench while processing actual inventory: inspect drives, preserve any authorized data worth recovering, validate the media, generate appropriate documentation, and prepare usable drives for resale or reuse.
