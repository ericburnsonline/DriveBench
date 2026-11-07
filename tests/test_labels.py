
"""Non-destructive tests for DriveBench bag-label PDFs and CLI menus."""

import re
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
    """Return a new record containing one completed full-scan result."""
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
        record = dict(
            RECORD,
            raw_analysis=[RECORD["raw_analysis"][1]]
        )
        data = labels.label_data(DISK, record)

        self.assertIn("not recorded", data.scan_summary)
        self.assertEqual(
            data.pattern_summary,
            "Media pattern: not verified"
        )

    def test_partial_scan_cannot_claim_full_surface(self):
        data = labels.label_data(
            DISK,
            record_with_result(fully_scanned=False)
        )

        self.assertIn("not verified", data.scan_summary)
        self.assertNotIn("0x6C", data.pattern_summary)

    def test_byte_count_must_match_capacity(self):
        data = labels.label_data(
            DISK,
            record_with_result(
                bytes_examined=CAPACITY - 512
            )
        )

        self.assertIn("not verified", data.scan_summary)
        self.assertEqual(
            data.pattern_summary,
            "Media pattern: not verified"
        )

    def test_sampled_pattern_not_called_verified(self):
        record = record_with_result(
            uniform_byte_pattern={
                "status": "SAMPLED_OR_PARTIAL_ONLY",
                "byte_hex": "0x6C"
            }
        )

        self.assertEqual(
            labels.label_data(DISK, record).pattern_summary,
            "Media pattern: not verified"
        )

    def test_nonuniform_result(self):
        record = record_with_result(
            uniform_byte_pattern={
                "status": "NOT_UNIFORM"
            }
        )

        self.assertEqual(
            labels.label_data(DISK, record).pattern_summary,
            "Media pattern: not uniform"
        )

    def test_manual_serial_is_not_hardware_serial(self):
        disk = dict(DISK, serial="")
        record = dict(
            RECORD,
            last_device=disk,
            manual_serial="LABEL123"
        )
        data = labels.label_data(disk, record)

        self.assertEqual(data.serial, "LABEL123")
        self.assertIn("OPERATOR", data.serial_caption)

    def test_asset_id_fallback(self):
        disk = dict(DISK, serial="")
        record = dict(
            RECORD,
            last_device=disk,
            asset_id="BIN-42"
        )
        data = labels.label_data(disk, record)

        self.assertEqual(data.serial, "BIN-42")
        self.assertIn("ASSET ID", data.serial_caption)

    def test_recovered_names_and_notes_excluded(self):
        record = dict(
            RECORD,
            notes=["private customer information"],
            recovered_files=["private-wallet.dat"]
        )
        output = str(
            asdict(labels.label_data(DISK, record))
        )

        self.assertNotIn("private customer", output)
        self.assertNotIn("wallet.dat", output)

    def test_default_filename_remains_2p25x4(self):
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

    def test_three_sizes_have_distinct_filenames(self):
        names = {
            size: labels.label_filename(
                DISK, RECORD, size=size
            )
            for size in ("3x1", "2.25x4", "4x6")
        }

        self.assertEqual(len(set(names.values())), 3)
        self.assertTrue(
            names["3x1"].endswith("-bag-label-3x1.pdf")
        )
        self.assertTrue(
            names["4x6"].endswith("-bag-label-4x6.pdf")
        )

    def test_unknown_size_rejected(self):
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

    def test_all_three_pdfs_have_correct_physical_page_sizes(self):
        sizes = (
            ("3x1", 216, 72),
            ("2.25x4", 162, 288),
            ("4x6", 288, 432)
        )

        for size, width, height in sizes:
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

                    media_box = re.search(
                        rb"/MediaBox\s*\[\s*"
                        rb"([\d.]+)\s+([\d.]+)\s+"
                        rb"([\d.]+)\s+([\d.]+)\s*\]",
                        body
                    )

                    self.assertIsNotNone(
                        media_box,
                        "PDF has no /MediaBox"
                    )

                    coords = [
                        float(x) for x in media_box.groups()
                    ]
                    self.assertEqual(
                        coords,
                        [0.0, 0.0, float(width), float(height)]
                    )

                    self.assertIn(
                        b"github.com/ericburnsonline/DriveBench",
                        body
                    )

    def test_compact_layout_contains_full_hardware_serial_and_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                labels,
                "_fit_text",
                wraps=labels._fit_text
            ) as draw:
                labels.generate_label(
                    DISK,
                    RECORD,
                    size="3x1",
                    output_dir=tmp
                )

            text = "\n".join(
                str(call.args[1])
                for call in draw.call_args_list
            )

            self.assertIn("SEAGATE", text)
            self.assertIn("ST318406LW", text)
            self.assertIn("DB-000002", text)
            self.assertIn("HW SN: " + SERIAL, text)
            self.assertIn("FULL READ COMPLETE", text)
            self.assertIn("0x6C", text)
            self.assertIn("See full report", text)

    def test_compact_layout_marks_operator_serial(self):
        disk = dict(DISK, serial="")
        record = dict(
            RECORD,
            last_device=disk,
            manual_serial="LABEL123"
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                labels,
                "_fit_text",
                wraps=labels._fit_text
            ) as draw:
                labels.generate_label(
                    disk,
                    record,
                    size="3x1",
                    output_dir=tmp
                )

            text = "\n".join(
                str(call.args[1])
                for call in draw.call_args_list
            )

            self.assertIn(
                "OPERATOR ID: LABEL123",
                text
            )
            self.assertNotIn("HW SN:", text)

    def test_compact_layout_does_not_claim_unverified_read_passed(self):
        record = record_with_result(
            fully_scanned=False
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                labels,
                "_fit_text",
                wraps=labels._fit_text
            ) as draw:
                labels.generate_label(
                    DISK,
                    record,
                    size="3x1",
                    output_dir=tmp
                )

            text = "\n".join(
                str(call.args[1])
                for call in draw.call_args_list
            )

            self.assertIn("SCAN NOT VERIFIED", text)
            self.assertIn("Pattern not verified", text)
            self.assertNotIn("FULL READ COMPLETE", text)

    def test_compact_refuses_serial_that_cannot_fit(self):
        disk = dict(
            DISK,
            serial="Z" * 500
        )

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                reports.ReportError,
                "too long"
            ):
                labels.generate_label(
                    disk,
                    RECORD,
                    size="3x1",
                    output_dir=tmp
                )

    def test_label_private_and_regeneration_requires_explicit_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = labels.generate_label(
                DISK,
                RECORD,
                size="3x1",
                output_dir=tmp
            )

            self.assertEqual(
                first.stat().st_mode & 0o777,
                0o600
            )

            with self.assertRaises(reports.ReportError):
                labels.generate_label(
                    DISK,
                    RECORD,
                    size="3x1",
                    output_dir=tmp
                )

            second = labels.generate_label(
                DISK,
                RECORD,
                size="3x1",
                output_dir=tmp,
                replace=True
            )

            self.assertEqual(first, second)
            self.assertTrue(second.is_file())

    def test_label_generation_does_not_print(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                reports,
                "print_pdf"
            ) as printer:
                labels.generate_label(
                    DISK,
                    RECORD,
                    size="3x1",
                    output_dir=tmp
                )
                printer.assert_not_called()

    def test_long_serial_is_not_silently_truncated_on_large_labels(self):
        disk = dict(
            DISK,
            serial="Z" * 500
        )

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                reports.ReportError,
                "too long"
            ):
                labels.generate_label(
                    disk,
                    RECORD,
                    output_dir=tmp
                )


class LabelMenuTests(unittest.TestCase):

    def test_drive_menu_option_13_opens_labels(self):
        with patch.object(
            cli,
            "_ask",
            return_value="13"
        ), patch.object(
            cli,
            "_bag_label_menu"
        ) as selected:
            cli._disk_menu(dict(DISK))

        selected.assert_called_once_with(DISK)

    def test_history_option_5_works_without_connected_drive(self):
        with patch.object(
            cli.store,
            "read_state",
            return_value={
                "records": {
                    RECORD["id"]: RECORD
                }
            }
        ), patch.object(
            cli,
            "_ask",
            side_effect=["5", "0"]
        ), patch.object(
            cli,
            "_create_bag_label"
        ) as create:
            cli._single_history_menu(
                RECORD["id"]
            )

        create.assert_called_once_with(DISK, RECORD)

    def test_live_label_refuses_changed_identity(self):
        with patch.object(
            cli,
            "snapshot",
            return_value={
                "disks": [dict(DISK)]
            }
        ), patch.object(
            cli.processing,
            "_same",
            return_value=False
        ):
            with self.assertRaisesRegex(
                cli.UserError,
                "identity"
            ):
                cli._bag_label_menu(DISK)

    def test_size_menu_lists_all_three_formats_compact_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                cli.Path,
                "home",
                return_value=Path(tmp)
            ), patch.object(
                cli,
                "_pick",
                return_value=None
            ) as choose:
                cli._create_bag_label(DISK, RECORD)

        sizes = choose.call_args.args[0]

        self.assertEqual(
            sizes,
            ["3x1", "2.25x4", "4x6"]
        )

    def test_size_menu_passes_three_by_one_to_generator(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                cli.Path,
                "home",
                return_value=Path(tmp)
            ), patch.object(
                cli,
                "_pick",
                return_value=(0, "3x1")
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
            "3x1"
        )
        self.assertFalse(
            generate.call_args.kwargs["replace"]
        )
        printer.assert_not_called()

    def test_existing_compact_label_needs_regenerate_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_dir = (
                Path(tmp)
                / "DriveBench-Reports"
                / "labels"
            )
            target_dir.mkdir(parents=True)

            target = target_dir / labels.label_filename(
                DISK,
                RECORD,
                size="3x1"
            )
            target.write_bytes(
                b"keep the existing label"
            )

            with patch.object(
                cli.Path,
                "home",
                return_value=Path(tmp)
            ), patch.object(
                cli,
                "_pick",
                return_value=(0, "3x1")
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
                b"keep the existing label"
            )

    def test_legacy_default_size_still_available_from_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                cli.Path,
                "home",
                return_value=Path(tmp)
            ), patch.object(
                cli,
                "_pick",
                return_value=(1, "2.25x4")
            ), patch.object(
                cli.labels,
                "generate_label"
            ) as generate:
                generate.return_value = (
                    Path(tmp) / "label.pdf"
                )
                cli._create_bag_label(DISK, RECORD)

        self.assertEqual(
            generate.call_args.kwargs["size"],
            "2.25x4"
        )


if __name__ == "__main__":
    unittest.main()
