
# Changelog

## 0.4.5 — Antistatic bag label PDFs

- Add printable PDF identification labels for antistatic drive bags.
- Support three label sizes:
  - **3 × 1 inches** — compact horizontal label for smaller label stock.
  - **2.25 × 4 inches** — original default label size.
  - **4 × 6 inches** — larger alternative.
- Add a dedicated 3 × 1-inch layout designed to fit essential drive information without shrinking the original layout.
- Include manufacturer, model, interface, decimal capacity, and available serial number.
- Include the DriveBench inventory record ID when available.
- Distinguish hardware-reported serial numbers from operator-supplied serial numbers and asset IDs.
- Display the full available serial number without silently truncating it.
- Use compact status descriptions such as `FULL READ COMPLETE` and `SCAN NOT VERIFIED` on the 3 × 1-inch label.
- Include verified uniform-byte patterns, such as `0x6C`, when supported by completed full-surface scans.
- Prevent incomplete, failed, or partially sampled scans from being presented as completed full-surface verification.
- Include a reference to the detailed test report and the DriveBench GitHub project URL.
- Include the DriveBench version and a sanitization disclaimer on the larger label formats.
- Generate PDFs at the exact selected physical label dimensions.
- Save labels under `~/DriveBench-Reports/labels/`, separate from letter-size drive reports.
- Generate descriptive filenames containing drive identification and label dimensions.
- Use distinct filename suffixes:
  - `-bag-label-3x1.pdf`
  - `-bag-label-2p25x4.pdf`
  - `-bag-label-4x6.pdf`
- Require explicit confirmation before replacing an existing label PDF.
- Support generating labels from persistent drive history after the physical drive has been disconnected.
- Add **13. Generate antistatic bag label PDF** to the selected-drive menu.
- Add **5. Generate / regenerate an antistatic bag label PDF** to the saved drive-history menu.
- Present the 3 × 1-inch compact format first in the interactive size selection.
- Preserve the 2.25 × 4-inch default for existing programmatic callers.
- Keep recovered filenames, personal file contents, and internal recovery history out of seller-facing labels.
- Extend automated regression coverage to include all three PDF sizes, complete serial numbers, operator IDs, verified scan summaries, safe regeneration, and menu integration.
- Generate PDFs without automatically submitting printer jobs.
- Keep direct Zebra/ZPL printing outside the scope of this release.
- Keep the selected drive active after exiting analysis, preparation, identification, report, and label submenus rather than returning to the main menu.
- Refresh and revalidate the selected drive before the next operation; require explicit reselection if the device path, identity, or associated record changes.
- Show the installed DriveBench version in selected-drive and other submenu headings for easier version troubleshooting.
- Expand mocked regression tests for persistent navigation and drive-identity changes without accessing physical disks.

**Label limitations:** Bag labels summarize recorded diagnostic results. They do not certify secure sanitization, factory-new condition, or the absence of previously stored data. Generating a label does not modify the source disk.

**Validation status:** Automated regression testing and physical printing of the new 3 × 1-inch format must be completed before merging this release.

**Future reporting improvements:** Per-drive output folders, consistent sortable filenames across all artifacts, and marketplace-ready PNG images are planned for v0.4.6. Wipe-operation verification details should also be incorporated into future persistent logs and seller-facing reports.

## 0.4.4 — Printable PDF drive reports

- Add seller-facing, letter-size PDF drive test reports generated from DriveBench identification, diagnostic, and persistent analysis records.
- Include manufacturer, model, interface, capacity, hardware serial, optional operator-supplied identifiers, and available completed test results.
- Include the latest completed full raw-disk scan, including full-surface uniform-byte verification results when available.
- Support optional collection of current SMART/SCSI health information for inclusion in the generated report. Missing or unavailable test results are never represented as passed.
- Save reports on the Ubuntu workstation under `~/DriveBench-Reports/`, without modifying or formatting the tested drive.
- Generate descriptive PDF filenames combining drive manufacturer, interface, model, capacity, and serial number or another explicitly identified record.
- Allow existing PDFs to be regenerated with confirmation, incorporating additional test results as DriveBench evolves.
- Support PDF regeneration from persistent drive history even after the physical drive has been disconnected.
- Add optional CUPS printing using the Ubuntu system's default printer or a selected configured printer, with explicit print confirmation.
- Include the DriveBench version and GitHub project URL in the PDF footer.
- Keep PDF reporting separate from the existing on-drive `DRIVEBENCH-REPORT.txt` feature.
- Exclude recovered file contents, personal filenames, and internal recovery history from seller-facing reports.
- Add an extensible report-section structure for integrating future diagnostics and test results.
- Extend the dependency checker to detect optional ReportLab (`python3-reportlab`) and CUPS (`cups-client`) requirements without preventing other DriveBench functionality.
- Add 14 automated regression tests covering report generation, filenames, regeneration, CUPS commands, privacy-conscious reporting, and menu integration.
- Reserve alternate label sizes and antistatic-bag label layouts for a future enhancement.

**Reporting limitations:** PDF reports summarize recorded diagnostic information and do not certify secure sanitization, factory-new condition, or the absence of previously stored user data. Generating or printing a PDF does not alter the source drive.

## 0.4.3 — Uniform-byte verification

- Track a potential constant-byte pattern across **every byte read** during raw evidence scans, including `0x00`, `0xFF`, `0x6C`, or any other byte value.
- Report `VERIFIED_FULL_SURFACE` only after a completed full scan reads the entire reported logical disk capacity; otherwise distinguish `SAMPLED_OR_PARTIAL_ONLY`, `NOT_UNIFORM`, and `NO_DATA`.
- Include the byte value and byte count in the scan summary and persistent raw-analysis history; no extra disk-writing action is added.
- Avoid treating a uniform pattern as proof of factory-new (NOS) condition, prior wiping, or certified sanitization.
- Add regression tests for uniform patterns, mismatches, incomplete/sampled scans, reporting, and history.

## 0.4.2

- Add read-only quick and complete raw-disk evidence scans, signature candidates, structural hints, zero-byte fraction, and entropy statistics.
- Add read-only TestDisk partition investigation and guarded PhotoRec/ddrescue recovery/imaging to a separate verified physical disk.
- Persist raw scan events and results across restarts, recording incomplete operations rather than claiming success.
- Fix sudo authentication behavior for the full scan to preserve the controlling terminal/session.
- On an Ubuntu SCSI test bench, successfully read the full 17.09 GiB user-addressable space of a Seagate ST318406LW; quick and full raw scans were exercised on hardware.
- **Not validated on real hardware yet:** destructive wipe/format operations and external recovery/imaging integration. Continue using a sacrificial HDD for eventual testing.

## 0.4.1

- Add a Drive Identification menu for automatic native SMART/SCSI serial inquiry, sudo retry, and manual serial / asset ID entry.
- Store manually entered identifiers separately from device-reported values, atomically in persistent history.
- Create manual-only records for drives without trustworthy hardware identifiers; require explicit reattachment after reboot.
- Store processing status, wipe/format/report events, and interruptions on operator-attached records, with another physical-label confirmation for destructive actions.
- Include provenance-labeled serials and asset IDs in on-disk drive reports.
- Add tests for non-root SCSI inquiry, USB bridge uncertainty, power-loss persistence, and record safety.

## 0.4.0

- Add a menu option for supervised whole-drive zero/random overwrite (1–10 passes).
- Add optional full zero readback verification or random-data readability check.
- Record operation start, each completed pass, failures, and successful completion.
- Create MBR/FAT32 (legacy) or GPT/exFAT (modern) layouts.
- Add on-disk `DRIVEBENCH-REPORT.txt` using saved before-wipe diagnostics.
- Block writes to protected, mounted, mapped, read-only, or detected solid-state storage.
- Use kernel disk sequence ID, when available, for same-session device checks.
- Retain 0.3 diagnostics, GitHub branch/update download, and SCSI conflict warnings.

**Development note:** The destructive paths have only been tested with mocked block-device commands. Verify all behavior on a sacrificial conventional HDD before regular use.
