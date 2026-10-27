# Changelog

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
