
# DriveBench: Ubuntu drive inspection, recovery, and reporting

DriveBench is a terminal-based drive inspection and preparation utility for Ubuntu Linux. It is designed for workstations testing SCSI, IDE, SATA, and USB-connected storage devices.

DriveBench uses numbered menus rather than command-line subcommands.

Launch from the project directory:

```bash
bash start-drivebench.sh
```

Or:

```bash
python3 -m drivebench
```

**Always run DriveBench as your normal user, not as root.**

## Features

- Discover disks, partitions, interfaces, serial numbers, SCSI addresses, and storage controllers.
- Identify drives using hardware serial numbers, operator-entered serial numbers, and asset IDs.
- Maintain persistent drive history across reboots.
- Protect system disks and support explicit drive, slot, and controller exclusions.
- Mount supported partitions read-only.
- Inspect filesystem and partition signatures.
- Collect SMART and SCSI health information.
- Perform quick or full read-only raw-media evidence scans.
- Detect recognizable file signatures and potential filesystem structures.
- Verify uniform-byte patterns across examined data.
- Record completed, interrupted, and failed analysis operations.
- Investigate lost partitions with TestDisk.
- Recover files using PhotoRec to a separate destination disk.
- Create disk images using GNU ddrescue.
- Perform supervised destructive overwrites and formatting.
- Generate detailed, printable PDF drive reports.
- Regenerate reports from saved history after disconnecting a drive.
- Submit letter-size PDF reports to configured Ubuntu CUPS printers.
- Generate PDF labels for antistatic drive bags.
- Support **3 × 1, 2.25 × 4, and 4 × 6-inch labels**.
- Include drive identification and available verified test results on labels.
- Check GitHub branches and download staged updates without automatically replacing installed code.

Not yet implemented: direct Zebra/ZPL label printing, marketplace report images, per-drive output folders, device-native SSD secure erase, dedicated write/read surface stress testing, and Macintosh HFS/Apple Partition Map creation.

## Installation

### Required Ubuntu packages

```bash
sudo apt update
sudo apt install -y python3 util-linux udev pciutils sudo coreutils
```

Additional diagnostic and preparation utilities:

```bash
sudo apt install -y smartmontools sg3-utils wget git parted dosfstools exfatprogs diffutils
```

Optional recovery utilities:

```bash
sudo apt install -y testdisk gddrescue
```

Optional PDF generation and printing:

```bash
sudo apt install -y python3-reportlab cups-client
```

Both full-page reports and bag-label PDFs use ReportLab.

Verify that ReportLab is available:

```bash
python3 -c "import reportlab; print(reportlab.Version)"
```

If DriveBench runs inside a Python virtual environment, ReportLab must also be installed in that environment.

CUPS is required only for submitting print jobs through Ubuntu. PDF generation does not require CUPS or a connected printer.

### Download DriveBench

Repository:

https://github.com/ericburnsonline/DriveBench

Select **Code → Download ZIP**, extract the archive, and open a terminal in the extracted directory.

Alternatively:

```bash
git clone https://github.com/ericburnsonline/DriveBench.git
cd DriveBench
```

For testing an unmerged feature, download or check out the appropriate branch instead of `main`.

### Start DriveBench

```bash
bash start-drivebench.sh
```

Run the automated tests:

```bash
bash run-tests.sh
```

DriveBench requests elevated privileges only for operations that require them.

Do not launch the complete application using `sudo`.

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

After selecting a drive:

```text
 1. View full drive details
 2. View partitions
 3. Mount a partition read-only
 4. Add disk exclusion
 5. View history / add note / manual wipe report
 6. Inspect filesystem / partition signatures
 7. Show SMART / SCSI drive-health details
 8. Show detailed SCSI log pages
 9. Wipe / format / write drive report (DESTRUCTIVE)
10. Identify drive / manually enter serial and asset ID
11. Raw disk analysis / evidence & recovery
12. Generate / print PDF drive report
13. Generate antistatic bag label PDF
 0. Back
```

## Printable PDF drive reports (v0.4.4)

DriveBench generates seller-facing, US Letter PDF reports containing saved drive identification and completed diagnostic results.

The PDFs are saved on the Ubuntu workstation. They are not written to the disk being tested.

**A drive does not need to be wiped, partitioned, or formatted to generate a PDF report.**

### Generate a report

1. Launch DriveBench.
2. Choose **2. Select drive**.
3. Select the correct physical drive.
4. Verify the model and serial number.
5. Choose **12. Generate / print PDF drive report**.
6. Select **1. Generate / regenerate PDF**.
7. Optionally include current SMART/SCSI information.
8. DriveBench displays the saved PDF location.

Existing reports require the explicit `REGENERATE` confirmation before replacement.

### Report location

Reports are saved under:

```text
~/DriveBench-Reports/
```

Example:

```text
SEAGATE-spi-ST318406LW-18GB-SN-3FE1WTKP00007310BJH2.pdf
```

Filenames are based on the available manufacturer, interface, model, capacity, and serial or alternative identifier.

### Report contents

Depending on available records, reports include:

- Manufacturer and model
- Interface and capacity
- Hardware-reported serial number
- Operator-supplied serial or asset ID, clearly distinguished
- DriveBench inventory ID
- Completed raw-media scans
- Full logical-surface read results
- Verified uniform-byte patterns
- Optional current SMART/SCSI information
- DriveBench version and GitHub URL

Missing information is not represented as a successful test.

Recovered filenames, personal file contents, and complete internal recovery history are not included in seller-facing PDFs.

### Regenerate from history

A drive does not need to remain connected.

Choose:

**Main menu → 8. Drive history → select record → 4. Generate / regenerate PDF**

The report uses saved information.

Live SMART readings that were never persisted cannot be reconstructed after disconnecting a drive.

### Print the full-page report

Choose:

**Select drive → 12 → 2. Print saved PDF**

DriveBench uses the Ubuntu CUPS system.

The initial print request specifies:

- US Letter
- Single-sided
- One copy

A CUPS job being accepted does not guarantee physical printing.

During development, a DriveBench-generated Seagate report was successfully printed from Windows after transferring the PDF from Ubuntu. Direct Ubuntu printing experienced CUPS/USB issues and remains a separate troubleshooting task.

## Antistatic drive-bag labels (v0.4.5)

DriveBench can generate smaller PDF identification labels for placement on the outside of antistatic bags.

These complement the detailed paper report and help identify drives after packaging.

### Supported label sizes

| Size | Description |
| --- | --- |
| **3 × 1 inches** | Compact horizontal label |
| **2.25 × 4 inches** | Detailed bag identification label |
| **4 × 6 inches** | Larger identification label |

The programmatic default remains **2.25 × 4 inches** for compatibility, while the interactive menu lists the new **3 × 1-inch** option first.

All dimensions refer to the actual PDF page size.

### Compact 3 × 1-inch labels

The 3 × 1-inch format uses a dedicated horizontal layout.

It prioritizes:

- DriveBench inventory ID
- Manufacturer
- Model
- Capacity
- Interface
- Full available serial number
- Completed full-surface read status
- Verified uniform-byte information, when available
- Reference to the detailed report
- DriveBench GitHub URL

The compact layout uses abbreviated descriptions to conserve space.

A hardware-reported serial is identified with `HW SN`.

An operator-entered identifier is labeled separately and is never presented as firmware-verified.

If a required identifier cannot fit, label generation raises an error instead of silently truncating the serial number.

### Generate a label for a connected drive

1. Launch DriveBench.
2. Choose **2. Select drive**.
3. Select and verify the physical drive.
4. Choose **13. Generate antistatic bag label PDF**.
5. Select a label size.

The size menu offers:

```text
ANTISTATIC BAG LABEL SIZE

 1. 3 x 1 inches (compact)
 2. 2.25 x 4 inches
 3. 4 x 6 inches
 0. Back
```

DriveBench saves the PDF without writing anything to the source disk or submitting a printer job.

If the file already exists, type `REGENERATE` to replace it.

### Generate a label from saved history

Choose:

**Main menu → 8. Drive history → select record → 5. Generate / regenerate an antistatic bag label PDF**

This allows labels to be generated after disconnecting the original drive.

When a record uses manually supplied identification, verify that it corresponds to the intended physical disk before applying the label.

### Label file location

Version 0.4.5 saves labels under:

```text
~/DriveBench-Reports/labels/
```

Example filenames:

```text
SEAGATE-spi-ST318406LW-18GB-SN-3FE1WTKP00007310BJH2-bag-label-3x1.pdf

SEAGATE-spi-ST318406LW-18GB-SN-3FE1WTKP00007310BJH2-bag-label-2p25x4.pdf

SEAGATE-spi-ST318406LW-18GB-SN-3FE1WTKP00007310BJH2-bag-label-4x6.pdf
```

The dimensions are included in filenames so different label formats do not overwrite one another.

Existing labels require explicit confirmation before regeneration.

**Per-drive folders and common sortable filename prefixes are planned for v0.4.6, not v0.4.5.**

### Verified scan results

Labels use saved completed full-scan events.

A full logical-surface read is described as complete only when the stored scan indicates full coverage of the reported disk capacity.

A uniform-byte pattern is described as verified only when the underlying result supports `VERIFIED_FULL_SURFACE`.

Incomplete, failed, or sampled scans must not be represented as completed full-surface verification.

Examples of possible summaries include:

```text
FULL READ COMPLETE
Uniform 0x6C: verified

SCAN NOT VERIFIED
Pattern not verified
```

A completed full-surface read does not prove that a drive is factory-new or securely sanitized.

Labels are inventory and diagnostic summaries, not sanitization certificates.

### Printing PDF labels

Version 0.4.5 **does not automatically print labels**.

Generate the PDF and print it using a suitable application and configured printer driver.

For Zebra printers:

1. Use a printer loaded with the correct label stock.
2. Select the matching label dimensions.
3. Print at **100% / Actual size**.
4. Disable scaling or fit-to-page.
5. Confirm the label prints without clipping.

For 3 × 1-inch stock, configure the printer or Windows driver for a 3-inch-wide, 1-inch-long label.

A thermal-transfer label requires a compatible ribbon. Direct-thermal labels do not.

The Zebra GK420t supports both methods, but the printer configuration and media must agree.

A blank printer configuration label indicates a printer/media problem rather than a DriveBench PDF problem.

Do not use DriveBench's letter-size CUPS printing option for small bag labels. It currently requests US Letter media.

### Packaging recommendations

Place the tested drive in a suitable antistatic bag.

Attach the identification label to the outside of the bag.

Keep the manufacturer's original drive label available for inspection.

Include the detailed full-page PDF report as a printed document when appropriate.

Avoid formatting an otherwise unformatted drive simply to add documentation.

## Raw disk analysis and recovery (v0.4.2)

Select:

**2. Select drive → 11. Raw disk analysis / evidence & recovery**

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

### Quick evidence scan

Reads selected areas of the disk.

Searches for recognizable filesystem structures and file signatures.

Results apply only to sampled regions.

### Full evidence scan

Reads the complete host-accessible logical surface, where possible.

Records:

- Number of bytes examined
- Scan completion
- Recognizable signatures
- Filesystem and partition hints
- Entropy observations
- Zero-byte fraction
- Uniform-byte analysis

Potential file signatures include JPEG, PNG, PDF, ZIP, GIF, ELF, SQLite, gzip, and RAR.

Signature matches are only candidates. They do not prove that complete files can be recovered.

No detected signature does not prove that the drive was never used.

### Uniform-byte verification (v0.4.3)

Uniform-byte analysis is integrated into the quick and full scans.

It supports any byte value, including:

- `0x00`
- `0xFF`
- `0x6C`

Possible results:

| Result | Meaning |
| --- | --- |
| `VERIFIED_FULL_SURFACE` | Completed full logical-surface read with a uniform byte pattern |
| `SAMPLED_OR_PARTIAL_ONLY` | Examined data matches, but entire disk coverage is not established |
| `NOT_UNIFORM` | Multiple byte values detected |
| `NO_DATA` | No bytes examined |

In October 2026, DriveBench successfully read all 18,351,959,040 host-addressable bytes of a Seagate ST318406LW and verified a uniform `0x6C` byte pattern.

That result does not establish the origin of the pattern or certify sanitization.

### TestDisk

TestDisk examines partition structures and can search for lost partitions.

A successful process exit code is not evidence that partitions were found or that a search completed.

Current DriveBench TestDisk output handling has limitations. Detailed result interpretation and clearer completion reporting are planned improvements.

Do not select TestDisk's partition-table **Write** function when investigating a disk that should remain unchanged.

### PhotoRec

PhotoRec can attempt file recovery without relying on a working partition table.

Recovered files must be written to a separate physical disk.

DriveBench should not be used to recover files back onto the source disk.

Recovery may expose previously stored information. Handle recovered material only when authorized.

### GNU ddrescue

DriveBench can invoke ddrescue to create an image on a different physical disk.

The destination must have sufficient available space.

The saved image and recovery mapfile should be retained together when performing resumable imaging.

These recovery workflows require additional real-hardware validation.

### Persistent analysis history

DriveBench saves analysis results under its persistent state location:

```text
~/.local/state/drivebench/state.json
```

History can include:

- Drive identification
- Operation type
- Start and completion times
- Status
- Scan coverage
- Signature observations
- Uniform-byte verification results

Interrupted and failed operations are not marked as successfully completed.

## Drive identification (v0.4.1)

Choose:

**Select drive → 10. Identify drive**

DriveBench can attempt native serial detection using SMART and SCSI inquiry utilities.

Hardware-reported serial numbers are distinguished from operator-supplied values.

An operator may enter:

- Manual serial number
- Asset or inventory ID

Manually supplied identifiers persist in history but do not constitute proof of hardware identity.

USB bridge serial numbers are not automatically assumed to belong to the actual disk.

Drives without trustworthy hardware identification require manual confirmation when reconnecting saved records after a restart.

## Wiping and formatting (v0.4.0)

**WARNING: These operations destroy existing data.**

Complete any necessary inspection, imaging, or authorized recovery before wiping or formatting.

Choose:

**Select drive → 9. Wipe / format / write drive report**

### Whole-disk overwrite

DriveBench supports supervised zero or random overwrites with 1–10 passes.

Optional verification includes:

- Full zero readback
- Readability testing after random overwrites

Random-data readability checking does not establish exact byte-for-byte verification of the random stream.

Incomplete operations must not be reported as successful wipes.

### Partitioning and formatting

Supported options include:

- FAT32 with MBR
- exFAT with GPT

Formatting is separate from wiping.

Legacy compatibility varies by operating system and storage controller.

### On-drive report

DriveBench can write `DRIVEBENCH-REPORT.txt` to an appropriate formatted partition.

This differs from printable PDFs, which are stored on the Ubuntu workstation and do not modify the tested disk.

### Destructive-operation safeguards

DriveBench performs checks involving:

- System-disk protection
- Device identity
- Device size and model
- Disk sequence information when available
- Mounted filesystems
- Storage dependencies
- Configured exclusions
- Read-only device status
- Unsupported solid-state targets

Do not hot-swap SCSI devices.

**Destructive wipe and format workflows still require validation on a sacrificial physical HDD before routine use.**

Software overwriting does not guarantee sanitization of inaccessible or reallocated physical sectors.

## Read-only partition mounting

Choose:

**Select drive → 3. Mount a partition read-only**

DriveBench creates managed mount directories under:

```text
/mnt/drivebench/
```

Example:

```text
/mnt/drivebench/DB-000001-sdc1-001/
```

DriveBench tracks managed mount history and checks active kernel mount information.

Read-only software mounting is not a substitute for hardware write blocking when forensic integrity is required.

Unsupported or damaged filesystems may need specialist tools.

Older Macintosh formats and unusual vintage SCSI layouts may not be supported by standard Linux filesystem drivers.

## Filesystem signatures

DriveBench uses read-only commands such as:

```bash
blkid -p
wipefs --no-act
```

A missing partition table or unknown filesystem does not prove the disk is blank.

Unknown filesystem layouts can contain data that the installed Linux tools do not recognize.

## SMART and SCSI health information

Choose:

- **7. Show SMART / SCSI drive-health details**
- **8. Show detailed SCSI log pages**

Depending on device support, results may include:

- Model and serial
- Device-reported health status
- Power-on counters
- Start/stop counts
- Corrected and uncorrected errors
- Read/write error information
- SCSI grown defect counts

Missing information is not automatically interpreted as zero.

On some older SCSI drives, GLTSD settings can prevent historical counters from being preserved across power cycles.

Very low reported power-on hours do not prove factory-new condition.

SMART health checks do not replace surface testing.

## GitHub update checking

Choose:

**Main menu → 10. Check GitHub for branch updates / download ZIP**

DriveBench can discover available branches, display commit identifiers, download an archive, and compare files.

It does not install downloaded updates automatically.

For a Git checkout:

```bash
git status
git fetch origin
git switch main
git pull --ff-only origin main
```

To test an unmerged feature branch, check out that branch separately.

Persistent drive history and configuration are stored outside the source directory.

## SCSI ID troubleshooting

During hardware development, a Seagate ST318406LW appeared at multiple SCSI target IDs because of a hardware configuration conflict.

Correcting the SCSI ID jumper after powering down resolved the issue.

Useful command:

```bash
lsblk -d -o NAME,HCTL,SIZE,MODEL
```

When troubleshooting SCSI:

- Shut down before changing cables, jumpers, or drives.
- Ensure devices have unique bus IDs.
- Verify controller settings.
- Check termination and cabling.
- Do not assume duplicate models alone prove a conflict.

## Configuration and persistent state

Default locations:

```text
~/.config/drivebench/config.json
~/.local/state/drivebench/state.json
```

These files survive ordinary application upgrades and reboots.

DriveBench can record statuses such as:

- `DISCOVERED`
- `UNVERIFIED`
- `PROCESSING_INCOMPLETE`
- `WIPED`
- `FORMATTED_NOT_WIPED`
- `WIPED_AND_FORMATTED`
- `PREPARED`
- `MANUAL_WIPE_REPORTED`

A manually reported external wipe is not represented as a verified DriveBench operation.

## Testing

Run all automated tests:

```bash
bash run-tests.sh
```

Or:

```bash
python3 -m unittest discover -s tests -v
```

Run just the bag-label tests:

```bash
python3 -m unittest discover -s tests -p 'test_labels.py' -v
```

The v0.4.5 label tests are intended to cover:

- All three supported PDF page sizes
- Full serial number preservation
- Hardware versus operator-supplied identifiers
- Completed and incomplete scan summaries
- Uniform-byte verification safeguards
- Deterministic filenames
- Regeneration confirmation
- Generation from saved history
- CLI menu integration
- No automatic printer submission

Tests use simulated drive records rather than destructive operations on physical hardware.

The expanded test suite must pass before v0.4.5 is merged.

The 3 × 1-inch layout also requires a successful physical print before its printer compatibility can be considered validated.

## Planned development

### v0.4.6: Organized reports and marketplace images

Planned improvements:

- Create a dedicated output folder for each drive.
- Use the persistent DriveBench record ID as the primary folder identifier.
- Standardize a shared sortable filename prefix across all drive artifacts.
- Keep reports, labels, images, and future diagnostics together.
- Render the detailed drive-report PDF into a matching high-resolution PNG.
- Allow marketplace images to be used in eBay and Facebook Marketplace listings.
- Preserve the same verified test information across PDF and image exports.
- Avoid automatically deleting or relocating existing legacy reports.

Proposed folder structure:

```text
~/DriveBench-Reports/
  DB-000003-SEAGATE-ST318406LW/
    DB-000003-SEAGATE-ST318406LW-report.pdf
    DB-000003-SEAGATE-ST318406LW-report.png
    DB-000003-SEAGATE-ST318406LW-label-3x1.pdf
    DB-000003-SEAGATE-ST318406LW-label-2p25x4.pdf
    DB-000003-SEAGATE-ST318406LW-label-4x6.pdf
```

The final filename convention should incorporate the hardware serial when available, while keeping a stable DriveBench record ID as the primary identifier.

### Additional planned improvements

- Native ZPL printing for compatible Zebra printers.
- Improved TestDisk result interpretation and search completion reporting.
- Clearer PhotoRec source and destination verification.
- Free-space checks before recovery.
- Smart File Discovery and Recovery from mounted filesystems.
- Configurable discovery profiles for documents, images, videos, and backups.
- Selective verified file copying to separate storage.
- Per-region surface maps showing zero-filled and nonzero data.
- Better persistent SMART history.
- Dedicated write/read surface stress testing.
- Additional vintage filesystems.
- Macintosh SE disk cloning and preservation.
- Batch report and label generation.
- Additional disk-image verification.
- Appropriate SSD-specific sanitization methods.

DriveBench development is guided by practical work processing real storage inventory: identify the disk, inspect its condition, preserve authorized data where appropriate, evaluate the media, document the results, and prepare the drive for resale or reuse.
