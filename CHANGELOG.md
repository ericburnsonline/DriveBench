
# Changelog

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
