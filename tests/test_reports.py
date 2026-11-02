"""Non-destructive PDF report and CUPS submission tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from drivebench import reports

DISK = {"path": "/dev/sdb", "vendor": "Seagate", "model": "ST318406LW",
        "tran": "scsi", "size": 18_351_959_040, "serial": "S3XYZ987"}
RECORD = {"id": "DB-000002", "last_device": DISK, "raw_analysis": [
    {"kind": "full_scan", "status": "INCOMPLETE_OR_FAILED", "finished_at": "2026-10-27T22:30:00+00:00",
     "result": {"uniform_byte_pattern": {"status": "VERIFIED_FULL_SURFACE", "byte_hex": "0x00"}, "fully_scanned": True}},
    {"kind": "full_scan", "status": "COMPLETED", "finished_at": "2026-10-31T01:08:56+00:00",
     "result": {"bytes_examined": 18_351_959_040, "fully_scanned": True,
                "uniform_byte_pattern": {"status": "VERIFIED_FULL_SURFACE", "byte_hex": "0x6C"},
                "assessment": "NO_RECOGNIZABLE_EVIDENCE_DETECTED", "structural_markers": []}},
]}


class ReportsTests(unittest.TestCase):
    def test_filename_uses_brand_interface_model_size_serial(self):
        self.assertEqual(reports.report_filename(DISK, RECORD),
                         "Seagate-scsi-ST318406LW-18GB-SN-S3XYZ987.pdf")

    def test_sanitizes_supplied_serial(self):
        d = dict(DISK, serial="../../../weird\\serial:e")
        name = reports.report_filename(d, RECORD)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn(":", name)

    def test_missing_serial_requires_record(self):
        with self.assertRaises(reports.ReportError):
            reports.report_filename(dict(DISK, serial=""), {})

    def test_untrusted_operator_serial_labeled(self):
        name = reports.report_filename(dict(DISK, serial=""), {"manual_serial": "label1"})
        self.assertIn("OperatorSN-label1", name)

    def test_only_latest_completed_full_scan_is_used(self):
        sec = reports.build_sections(DISK, RECORD)[1]
        rows = dict(sec.rows)
        self.assertEqual(rows["Uniform byte pattern"], "0x6C - VERIFIED_FULL_SURFACE")
        self.assertEqual(rows["Entire logical surface read"], "Completed")

    def test_no_scan_does_not_claim_pass(self):
        sections = reports.build_sections(DISK, {})
        self.assertIn("No completed scan", str(sections))
        self.assertNotIn("VERIFIED_FULL_SURFACE", str(sections))

    def test_extra_section_is_extensible(self):
        add = reports.ReportSection("Future test", (("Motor", "Spins normally"),))
        self.assertEqual(reports.build_sections(DISK, RECORD, extra_sections=[add])[-1], add)

    def test_private_pdf_creation_and_explicit_regeneration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = reports.generate_pdf(DISK, RECORD, output_dir=tmp)
            self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(reports.ReportError):
                reports.generate_pdf(DISK, RECORD, output_dir=tmp)
            again = reports.generate_pdf(DISK, RECORD, output_dir=tmp, replace=True,
                                         extra_sections=[reports.ReportSection("New tests", (("A", "B"),))])
            self.assertEqual(path, again)
            self.assertTrue(again.is_file())

    def test_does_not_print_as_side_effect_of_pdf_creation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(reports.subprocess, "run") as run:
            reports.generate_pdf(DISK, RECORD, output_dir=tmp)
            run.assert_not_called()

    def test_print_uses_lp_no_shell_and_no_sudo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.pdf"
            path.write_bytes(b"%PDF-sample")
            with patch.object(reports.shutil, "which", return_value="/usr/bin/lp"), \
                 patch.object(reports.subprocess, "run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "request id is myprinter-4"
                outcome = reports.print_pdf(path, printer="myprinter")
                args = run.call_args.args[0]
                self.assertEqual(args[:3], ["lp", "-d", "myprinter"])
                self.assertNotIn("sudo", args)
                self.assertEqual(args[-1], str(path.resolve()))
                self.assertNotIn("shell", run.call_args.kwargs)
                self.assertIn("request id", outcome)

    def test_reject_non_pdf_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.pdf"
            path.write_text("not actually pdf")
            with self.assertRaises(reports.ReportError):
                reports.print_pdf(path)

    def test_list_printers(self):
        with patch.object(reports.shutil, "which", return_value="/usr/bin/lpstat"), \
             patch.object(reports.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "Office\nLabelPrinter\n"
            self.assertEqual(reports.list_printers(), ["Office", "LabelPrinter"])


class ReportMenuTests(unittest.TestCase):
    def test_drive_menu_reaches_pdf_report_option(self):
        from drivebench import cli
        d = dict(DISK, record_id='DB-000002', status='DISCOVERED',
                 reasons=[], confidence='strong', partitions=[])
        with patch.object(cli, '_ask', return_value='12'), \
             patch.object(cli, '_pdf_report_menu') as menu:
            cli._disk_menu(d)
        menu.assert_called_once_with(d)

    def test_history_can_regenerate_when_disk_disconnected(self):
        from drivebench import cli
        rec = dict(RECORD, last_device=DISK, status='DISCOVERED')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(cli.store, 'read_state', return_value={'records': {'DB-000002': rec}}), \
             patch.object(cli.Path, 'home', return_value=Path(tmp)), \
             patch.object(cli, '_ask', side_effect=['4','0']), \
             patch.object(cli.reports, 'generate_pdf', return_value=Path(tmp) / 'report.pdf') as generate:
            cli._single_history_menu('DB-000002')
            generate.assert_called_once()
            self.assertEqual(generate.call_args.args[0], DISK)
            self.assertEqual(generate.call_args.args[1], rec)


if __name__ == '__main__':
    unittest.main()
