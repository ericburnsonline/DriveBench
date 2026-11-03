
"""Non-destructive tests for antistatic bag PDF labels and CLI menus."""

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from drivebench import cli, labels, reports


CAPACITY = 18_351_959_040
SERIAL = "3FE1WTKP00007310BJH2"

DISK = {
    "path": "/dev/sdb",
    "vendor": "SEAGATE",
    "model": "ST318406LW",
    "tran": "spi",
    "size": CAPACITY,
    "serial": SERIAL,
    "identity": "scsi:seagate-test",
    "record_id": "DB-000002",
    "status": "DISCOVERED",
    "reasons": [],
    "confidence": "strong",
    "partitions": [],
}

RECORD = {
    "id": "DB-000002",
    "status": "DISCOVERED",
    "last_device": DISK,
    "raw_analysis": [
        {
            "kind": "full_scan",
            "status": "COMPLETED",
            "finished_at": "2026-10-31T01:25:41+00:00",
            "result": {
                "fully_scanned": True,
                "bytes_examined": CAPACITY,
                "uniform_byte_pattern": {
                    "status": "VERIFIED_FULL_SURFACE",
                    "byte_hex": "0x6C",
                },
                "assessment": "NO_RECOGNIZABLE_EVIDENCE_DETECTED",
            },
        },
        {
            "kind": "full_scan",
            "status": "INCOMPLETE_OR_FAILED",
            "finished_at": "2026-11-01T02:00:00+00:00",
            "result": {
                "fully_scanned": True,
                "bytes_examined": CAPACITY,
                "uniform_byte_pattern": {
                    "status": "VERIFIED_FULL_SURFACE",
                    "byte_hex": "0xFF",
                },
            },
        },
    ],
}


def record_with_result(**changes):
    """Build an independent saved record with one completed full scan."""
    scan = dict(RECORD["raw_analysis"][0])
    scan["result"] = dict(scan["result"], **changes)
    return dict(RECORD, raw_analysis=[scan])


class LabelDataTests(unittest.TestCase):

    def test_seagate_identity_and_verified_pattern(self):
        data = labels.label_data(DISK, RECORD)

        self.assertEqual(data.manufacturer, "SEAGATE")
        self.assertEqual(data.model, "ST318406LW")
        self.assertEqual(data.interface, "SCSI (SPI)")
        self.assertEqual(data.capacity, "18GB")
        self.assertEqual(data.serial, SERIAL)
        self.assertIn("DEVICE-REPORTED", data.serial_caption)
        self.assertEqual(data.inventory_id, "DB-000002")
        self.assertIn("read complete", data.scan_summary)
        self.assertIn("0x6C", data.pattern_summary)
        self.assertIn("verified", data.pattern_summary)

    def test_saved_history_works_without_connected_drive(self):
        data = labels.label_data({}, RECORD)

        self.assertEqual(data.model, "ST318406LW")
        self.assertEqual(data.serial, SERIAL)
        self.assertIn("0x6C", data.pattern_summary)

    def test_incomplete_scan_cannot_override_completed_scan(self):
        data = labels.label_data(DISK, RECORD)

        self.assertIn("0x6C", data.pattern_summary)
        self.assertNotIn("0xFF", data.pattern_summary)

    def test_no_completed_scan_does_not_claim_success(self):
        rec = dict(
            RECORD,
            raw_analysis=[RECORD["raw_analysis"][1]]
        )
        data = labels.label_data(DISK, rec)

        self.assertIn("not recorded", data.scan_summary)
        self.assertEqual(
            data.pattern_summary,
            "Media pattern: not verified"
        )

    def test_partial_scan_cannot_claim_full_surface(self):
        rec = record_with_result(fully_scanned=False)
        data = labels.label_data(DISK, rec)

        self.assertIn("not verified", data.scan_summary)
        self.assertNotIn("0x6C", data.pattern_summary)

    def test_byte_count_must_match_disk_capacity(self):
        rec = record_with_result(bytes_examined=CAPACITY - 512)
        data = labels.label_data(DISK, rec)

        self.assertIn("not verified", data.scan_summary)
        self.assertEqual(
            data.pattern_summary,
            "Media pattern: not verified"
        )

    def test_unverified_pattern_not_presented_as_verified(self):
        rec = record_with_result(
            uniform_byte_pattern={
                "status": "SAMPLED_OR_PARTIAL_ONLY",
                "byte_hex": "0x6C"
            }
        )
        data = labels.label_data(DISK, rec)

        self.assertEqual(
            data.pattern_summary,
            "Media pattern: not verified"
        )

    def test_nonuniform_result_is_described_correctly(self):
        rec = record_with_result(
            uniform_byte_pattern={"status": "NOT_UNIFORM"}
        )

        self.assertEqual(
            labels.label_data(DISK, rec).pattern_summary,
            "Media pattern: not uniform"
        )

    def test_operator_serial_is_not_mislabeled_as_hardware(self):
        disk = dict(DISK, serial="")
        rec = dict(
            RECORD,
            last_device=disk,
            manual_serial="LABEL123"
        )
        data = labels.label_data(disk, rec)

        self.assertEqual(data.serial, "LABEL123")
        self.assertIn("OPERATOR", data.serial_caption)

    def test_asset_id_fallback(self):
        disk = dict(DISK, serial="")
        rec = dict(
            RECORD,
            last_device=disk,
            asset_id="BIN-42"
        )
        data = labels.label_data(disk, rec)

        self.assertEqual(data.serial, "BIN-42")
        self.assertIn("ASSET ID", data.serial_caption)

    def test_no_recovered_filenames_or_notes_on_label(self):
        rec = dict(
            RECORD,
            notes=["private customer information"],
            recovered_files=["private-wallet.dat"]
        )
        printed = str(asdict(labels.label_data(DISK, rec)))

        self.assertNotIn("private customer", printed)
        self.assertNotIn("wallet.dat", printed)

    def test_filename_defaults_to_smaller_label(self):
        name = labels.label_filename(DISK, RECORD)

        self.assertTrue(
            name.startswith(
                "SEAGATE-spi-ST318406LW-18GB-SN-"
            )
        )
        self.assertTrue(
            name.endswith("-bag-label-2p25x4.pdf")
        )
        self.assertIn(SERIAL, name)

    def test_large_label_has_distinct_filename(self):
        name = labels.label_filename(
            DISK, RECORD, size="4x6"
        )

        self.assertTrue(name.endswith("-bag-label-4x6.pdf"))
        self.assertNotEqual(
            name,
            labels.label_filename(DISK, RECORD)
        )

    def test_unknown_label_size_is_rejected(self):
        with self.assertRaises(reports.ReportError):
            labels.label_filename(
                DISK, RECORD, size="Letter"
            )

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(reports.ReportError):
                labels.generate_label(
                    DISK,
                    RECORD,
                    size="3x5",
                    output_dir=tmp
                )


class LabelPDFTests(unittest.TestCase):

    def test_both_pdfs_have_correct_physical_page_sizes(self):
        for size, width, height in (
            ("2.25x4", 162, 288),
            ("4x6", 288, 432)
        ):
            with self.subTest(size=size):
                with tempfile.TemporaryDirectory() as tmp:
                    path = labels.generate_label(
                        DISK,
                        RECORD,
                        size=size,
                        output_dir=tmp
                    )
                    body = path.read_bytes()

                    self.assertTrue(body.startswith(b"%PDF-"))

                    expression = (
                        rb"/MediaBox\s*\[\s*0\s+0\s+"
                        + str(width).encode()
                        + rb"\s+"
                        + str(height).encode()
                        + rb"\s*\]"
                    )
                    self.assertRegex(body, expression)

                    self.assertIn(
                        b"github.com/ericburnsonline/DriveBench",
                        body
                    )

    def test_label_is_private_and_requires_explicit_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = labels.generate_label(
                DISK, RECORD, output_dir=tmp
            )

            self.assertEqual(
                first.stat().st_mode & 0o777,
                0o600
            )

            with self.assertRaises(reports.ReportError):
                labels.generate_label(
                    DISK, RECORD, output_dir=tmp
                )

            second = labels.generate_label(
                DISK,
                RECORD,
                output_dir=tmp,
                replace=True
            )

            self.assertEqual(first, second)
            self.assertTrue(second.is_file())

    def test_label_generation_does_not_print(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                reports, "print_pdf"
            ) as printer:
                labels.generate_label(
                    DISK, RECORD, output_dir=tmp
                )
                printer.assert_not_called()

    def test_serial_too_long_to_fit_is_rejected_not_truncated(self):
        disk = dict(DISK, serial="Z" * 500)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                reports.ReportError, "too long"
            ):
                labels.generate_label(
                    disk,
                    RECORD,
                    output_dir=tmp
                )


class LabelMenuTests(unittest.TestCase):

    def test_drive_menu_opens_label_option_13(self):
        with patch.object(
            cli, "_ask", return_value="13"
        ), patch.object(
            cli, "_bag_label_menu"
        ) as menu:
            cli._disk_menu(dict(DISK))

        menu.assert_called_once_with(DISK)

    def test_history_menu_can_generate_label_when_disconnected(self):
        with patch.object(
            cli.store,
            "read_state",
            return_value={
                "records": {RECORD["id"]: RECORD}
            }
        ), patch.object(
            cli, "_ask", side_effect=["5", "0"]
        ), patch.object(
            cli, "_create_bag_label"
        ) as generate:
            cli._single_history_menu(RECORD["id"])

        generate.assert_called_once_with(DISK, RECORD)

    def test_live_label_refuses_changed_drive_identity(self):
        with patch.object(
            cli,
            "snapshot",
            return_value={"disks": [dict(DISK)]}
        ), patch.object(
            cli.processing,
            "_same",
            return_value=False
        ):
            with self.assertRaisesRegex(
                cli.UserError, "identity"
            ):
                cli._bag_label_menu(DISK)

    def test_existing_label_requires_regenerate_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_dir = (
                Path(tmp) / "DriveBench-Reports" / "labels"
            )
            target_dir.mkdir(parents=True)

            target = target_dir / labels.label_filename(
                DISK, RECORD
            )
            target.write_bytes(b"keep this PDF")

            with patch.object(
                cli.Path,
                "home",
                return_value=Path(tmp)
            ), patch.object(
                cli,
                "_pick",
                return_value=(0, "2.25x4")
            ), patch.object(
                cli,
                "_ask",
                return_value="NO"
            ), patch.object(
                cli.labels,
                "generate_label"
            ) as generate:
                cli._create_bag_label(DISK, RECORD)

            generate.assert_not_called()
            self.assertEqual(
                target.read_bytes(),
                b"keep this PDF"
            )

    def test_new_label_uses_default_small_size_and_no_print(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                cli.Path,
                "home",
                return_value=Path(tmp)
            ), patch.object(
                cli,
                "_pick",
                return_value=(0, "2.25x4")
            ), patch.object(
                cli.labels,
                "generate_label"
            ) as generate, patch.object(
                reports,
                "print_pdf"
            ) as printer:

                generate.return_value = (
                    Path(tmp) / "label.pdf"
                )
                cli._create_bag_label(DISK, RECORD)

            self.assertEqual(
                generate.call_args.kwargs["size"],
                "2.25x4"
            )
            self.assertIs(
                generate.call_args.kwargs["replace"],
                False
            )
            printer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
