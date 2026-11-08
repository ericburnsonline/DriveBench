
"""Tests for DriveBench identification, state, and safe menu navigation."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from drivebench import __version__, cli, inventory, store
from drivebench.cli import snapshot, status_reasons


def mkdisk(path, serial, root=False):
    size = 5_000_000_000
    major_base = "8:0" if path == "/dev/sda" else "8:16"
    major_part = "8:1" if path == "/dev/sda" else "8:17"
    return {
        "path": path,
        "kname": Path(path).name,
        "size": size,
        "model": "TestDrive",
        "vendor": "ACME",
        "serial": serial,
        "wwn": "",
        "tran": "scsi",
        "pttype": "dos",
        "readonly": False,
        "removable": False,
        "controller_id": None,
        "slot": "scsi-slot:some-controller:0:1:0",
        "confidence": "strong",
        "identity": f"drive:key:{serial}",
        "identity_reason": "device serial",
        "system_reasons": [],
        "mountpoints": ["/"] if root else [],
        "partitions": [],
        "tree": {
            "path": path,
            "type": "disk",
            "maj:min": major_base,
            "mountpoints": [],
            "children": [{
                "type": "part",
                "path": path + "1",
                "maj:min": major_part,
                "mountpoints": ["/"] if root else []
            }],
        },
    }


class IdentityTests(unittest.TestCase):

    def test_scsi_identity_stable_across_device_name(self):
        a, confidence, _ = inventory.drive_identity(
            {
                "model": "Disk",
                "serial": "S-123",
                "vendor": "IBM",
                "size": 42,
                "tran": "scsi"
            },
            {}
        )
        b, confidence2, _ = inventory.drive_identity(
            {
                "model": "Disk",
                "serial": "S-123",
                "vendor": "IBM",
                "size": 42,
                "tran": "scsi"
            },
            {}
        )
        self.assertEqual(a, b)
        self.assertEqual(confidence, "strong")
        self.assertEqual(confidence2, "strong")

    def test_usb_bridge_serial_not_trusted(self):
        value, confidence, _ = inventory.drive_identity(
            {
                "model": "Adapter",
                "serial": "BRIDGE-123",
                "vendor": "USB",
                "size": 42,
                "tran": "usb"
            },
            {"ID_BUS": "usb"}
        )
        self.assertIsNone(value)
        self.assertEqual(confidence, "unverified")

    def test_usb_sat_serial_may_be_used(self):
        value, confidence, _ = inventory.drive_identity(
            {
                "model": "MyDrive",
                "serial": "BRIDGE-123",
                "vendor": "ATA",
                "size": 42,
                "tran": "usb"
            },
            {
                "ID_BUS": "usb",
                "ID_ATA_SERIAL": "Genuine-456"
            }
        )
        self.assertTrue(value.startswith("drive:"))
        self.assertEqual(confidence, "strong")

    def test_system_disk_protected_even_when_root_nested(self):
        root = mkdisk("/dev/sda", "OS", root=True)
        other = mkdisk("/dev/sdb", "DATA")
        with patch.object(
            inventory,
            "_root_majmin",
            return_value="8:1"
        ):
            protected, warnings = inventory.system_protection(
                [root, other]
            )
        self.assertIn("/dev/sda", protected)
        self.assertNotIn("/dev/sdb", protected)
        self.assertFalse(warnings)

    def test_fail_closed_when_root_unresolved(self):
        disk = mkdisk("/dev/sdc", "ONE")
        with patch.object(
            inventory,
            "_root_majmin",
            return_value="999:999"
        ):
            protected, warnings = inventory.system_protection(
                [disk]
            )
        self.assertIn("/dev/sdc", protected)
        self.assertTrue(warnings)

    def test_slot_exclusion_and_protected_root(self):
        disk = mkdisk("/dev/sdb", "AA", root=True)
        disk["system_reasons"] = ["root filesystem"]
        reasons = status_reasons(
            disk,
            {
                "ignored_drives": [],
                "ignored_slots": [disk["slot"]],
                "ignored_controllers": []
            }
        )
        self.assertTrue(
            any("SYSTEM" in reason for reason in reasons)
        )
        self.assertTrue(
            any("connection slot" in reason for reason in reasons)
        )


class StateTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env1 = patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(
                    Path(self.tmp.name) / "cfg"
                ),
                "XDG_STATE_HOME": str(
                    Path(self.tmp.name) / "state"
                )
            }
        )
        self.env1.start()
        self.addCleanup(self.env1.stop)
        store.initialize()

    def test_reboot_simulated_by_path_change_preserves_history(self):
        first = mkdisk("/dev/sdb", "DRIVE123")
        initial = store.remember_disks(
            [first]
        )["/dev/sdb"]
        store.record_manual_wipe(
            initial["id"],
            "zero",
            1,
            "Outside tool"
        )

        second = mkdisk("/dev/sdf", "DRIVE123")
        again = store.remember_disks(
            [second]
        )["/dev/sdf"]

        self.assertEqual(initial["id"], again["id"])
        self.assertEqual(
            again["status"],
            "MANUAL_WIPE_REPORTED"
        )
        self.assertEqual(
            store.read_state()["records"][
                initial["id"]
            ]["operations"][0]["source"],
            "operator_only_not_verified_by_drivebench"
        )

    def test_just_a_rescan_does_not_mark_a_disk_wiped(self):
        disk = mkdisk("/dev/sdc", "FRESH")
        x = store.remember_disks([disk])[disk["path"]]
        y = store.remember_disks([disk])[disk["path"]]

        self.assertEqual(x["status"], "DISCOVERED")
        self.assertEqual(y["status"], "DISCOVERED")

    def test_unverified_disk_is_not_attached_to_a_record(self):
        disk = mkdisk("/dev/sdd", "UNKNOWN")
        disk["identity"] = None
        disk["confidence"] = "unverified"

        self.assertEqual(store.remember_disks([disk]), {})
        self.assertEqual(
            store.read_state()["records"],
            {}
        )

    def test_exclusions_persist(self):
        store.add_exclusion(
            "ignored_controllers",
            "pci:03:abcd"
        )
        self.assertIn(
            "pci:03:abcd",
            store.read_config()["ignored_controllers"]
        )
        self.assertTrue(
            store.remove_exclusion(
                "ignored_controllers",
                "pci:03:abcd"
            )
        )
        self.assertNotIn(
            "pci:03:abcd",
            store.read_config()["ignored_controllers"]
        )

    def test_status_snapshot_from_mock_discovery(self):
        disk = mkdisk("/dev/sdc", "SERIAL")
        fake = {
            "disks": [disk],
            "controllers": [],
            "warnings": []
        }

        with patch(
            "drivebench.cli.inv.discover",
            return_value=fake
        ):
            result = snapshot()

        self.assertEqual(
            result["disks"][0]["record_id"],
            "DB-000001"
        )
        self.assertNotIn(
            "tree",
            result["disks"][0]
        )


class SelectedDriveNavigationTests(unittest.TestCase):
    """Menu navigation and identity checks; no actual disk access."""

    def setUp(self):
        self.disk = mkdisk(
            "/dev/sdb",
            "SEAGATE-001"
        )
        self.disk.update(
            record_id="DB-000003",
            status="DISCOVERED",
            reasons=[],
            partitions=[],
            diskseq=42,
        )

    def _inventory(self, disk=None):
        return {
            "disks": [dict(disk or self.disk)]
        }

    def test_refresh_returns_current_snapshot_not_old_object(self):
        refreshed = dict(
            self.disk,
            status="WIPED"
        )

        with patch.object(
            cli,
            "snapshot",
            return_value=self._inventory(refreshed)
        ):
            actual = cli._refresh_selected_disk(
                self.disk
            )

        self.assertEqual(
            actual["status"],
            "WIPED"
        )
        self.assertIsNot(
            actual,
            self.disk
        )

    def test_refresh_rejects_missing_device(self):
        with patch.object(
            cli,
            "snapshot",
            return_value={"disks": []}
        ):
            with self.assertRaisesRegex(
                cli.UserError,
                "missing"
            ):
                cli._refresh_selected_disk(
                    self.disk
                )

    def test_refresh_rejects_device_path_reassignment(self):
        renamed = dict(
            self.disk,
            path="/dev/sdf"
        )

        with patch.object(
            cli,
            "snapshot",
            return_value=self._inventory(renamed)
        ):
            with self.assertRaisesRegex(
                cli.UserError,
                "changed"
            ):
                cli._refresh_selected_disk(
                    self.disk
                )

    def test_refresh_rejects_different_serial_at_same_path(self):
        replacement = dict(
            self.disk,
            serial="SOMEONE-ELSES-DISK"
        )

        with patch.object(
            cli,
            "snapshot",
            return_value=self._inventory(replacement)
        ):
            with self.assertRaisesRegex(
                cli.UserError,
                "changed"
            ):
                cli._refresh_selected_disk(
                    self.disk
                )

    def test_refresh_rejects_new_disk_sequence_at_same_path(self):
        replacement = dict(
            self.disk,
            diskseq=43
        )

        with patch.object(
            cli,
            "snapshot",
            return_value=self._inventory(replacement)
        ):
            with self.assertRaisesRegex(
                cli.UserError,
                "changed"
            ):
                cli._refresh_selected_disk(
                    self.disk
                )

    def test_refresh_rejects_changed_persistent_record(self):
        different_record = dict(
            self.disk,
            record_id="DB-000004"
        )

        with patch.object(
            cli,
            "snapshot",
            return_value=self._inventory(different_record)
        ):
            with self.assertRaisesRegex(
                cli.UserError,
                "persistent record"
            ):
                cli._refresh_selected_disk(
                    self.disk
                )

    def test_refresh_rejects_duplicate_path_entries(self):
        entries = [
            dict(self.disk),
            dict(self.disk)
        ]

        with patch.object(
            cli,
            "snapshot",
            return_value={"disks": entries}
        ):
            with self.assertRaisesRegex(
                cli.UserError,
                "changed"
            ):
                cli._refresh_selected_disk(
                    self.disk
                )

    def test_drive_menu_reuses_selection_for_scan_report_and_label(self):
        actions = []

        with patch.object(
            cli,
            "snapshot",
            return_value=self._inventory()
        ) as scan, patch.object(
            cli,
            "_ask",
            side_effect=["11", "12", "13", "0"]
        ), patch.object(
            cli,
            "_raw_analysis_menu",
            side_effect=lambda d: actions.append(
                ("scan", d["record_id"])
            )
        ), patch.object(
            cli,
            "_pdf_report_menu",
            side_effect=lambda d: actions.append(
                ("report", d["record_id"])
            )
        ), patch.object(
            cli,
            "_bag_label_menu",
            side_effect=lambda d: actions.append(
                ("label", d["record_id"])
            )
        ), redirect_stdout(io.StringIO()):
            cli._disk_menu(self.disk)

        self.assertEqual(
            actions,
            [
                ("scan", "DB-000003"),
                ("report", "DB-000003"),
                ("label", "DB-000003")
            ]
        )
        self.assertEqual(scan.call_count, 3)

    def test_drive_menu_reuses_selection_after_preparation_and_identification(self):
        actions = []

        with patch.object(
            cli,
            "snapshot",
            return_value=self._inventory()
        ), patch.object(
            cli,
            "_ask",
            side_effect=["9", "10", "0"]
        ), patch.object(
            cli,
            "_processing_menu",
            side_effect=lambda d: actions.append(
                ("prepare", d["record_id"])
            )
        ), patch.object(
            cli,
            "_identification_menu",
            side_effect=lambda d: actions.append(
                ("identify", d["record_id"])
            )
        ), redirect_stdout(io.StringIO()):
            cli._disk_menu(self.disk)

        self.assertEqual(
            actions,
            [
                ("prepare", "DB-000003"),
                ("identify", "DB-000003")
            ]
        )

    def test_drive_menu_error_does_not_unselect_unchanged_drive(self):
        actions = []

        with patch.object(
            cli,
            "snapshot",
            return_value=self._inventory()
        ), patch.object(
            cli,
            "_ask",
            side_effect=["13", "11", "0"]
        ), patch.object(
            cli,
            "_bag_label_menu",
            side_effect=cli.UserError(
                "label could not be generated"
            )
        ), patch.object(
            cli,
            "_raw_analysis_menu",
            side_effect=lambda d: actions.append(
                d["record_id"]
            )
        ), redirect_stdout(io.StringIO()) as output:
            cli._disk_menu(self.disk)

        self.assertIn(
            "label could not be generated",
            output.getvalue()
        )
        self.assertEqual(
            actions,
            ["DB-000003"]
        )

    def test_drive_menu_refuses_next_operation_if_device_changed(self):
        replacement = dict(
            self.disk,
            serial="NEW-DISK",
            diskseq=43
        )

        with patch.object(
            cli,
            "snapshot",
            side_effect=[
                self._inventory(),
                self._inventory(replacement)
            ]
        ), patch.object(
            cli,
            "_ask",
            side_effect=["11", "9"]
        ), patch.object(
            cli,
            "_raw_analysis_menu"
        ) as scan, patch.object(
            cli,
            "_processing_menu"
        ) as prepare, redirect_stdout(
            io.StringIO()
        ) as output:
            cli._disk_menu(self.disk)

        scan.assert_called_once()
        prepare.assert_not_called()
        self.assertIn(
            "SELECTION INVALIDATED",
            output.getvalue()
        )

    def test_drive_menu_refreshes_status_between_operations(self):
        wiped = dict(
            self.disk,
            status="WIPED"
        )
        seen = []

        with patch.object(
            cli,
            "snapshot",
            side_effect=[
                self._inventory(),
                self._inventory(wiped)
            ]
        ), patch.object(
            cli,
            "_ask",
            side_effect=["11", "13", "0"]
        ), patch.object(
            cli,
            "_raw_analysis_menu",
            side_effect=lambda d: seen.append(
                d["status"]
            )
        ), patch.object(
            cli,
            "_bag_label_menu",
            side_effect=lambda d: seen.append(
                d["status"]
            )
        ), redirect_stdout(io.StringIO()):
            cli._disk_menu(self.disk)

        self.assertEqual(
            seen,
            ["DISCOVERED", "WIPED"]
        )

    def test_submenus_show_version_without_touching_disks(self):
        for submenu in (
            cli._processing_menu,
            cli._raw_analysis_menu
        ):
            with self.subTest(
                submenu=submenu.__name__
            ):
                with patch.object(
                    cli,
                    "_ask",
                    return_value="0"
                ), redirect_stdout(
                    io.StringIO()
                ) as output:
                    submenu(self.disk)

                self.assertIn(
                    f"DriveBench v{__version__}",
                    output.getvalue()
                )

        with patch.object(
            cli,
            "_ask",
            return_value="0"
        ), redirect_stdout(
            io.StringIO()
        ) as output:
            cli._disk_menu(self.disk)

        self.assertIn(
            f"DriveBench v{__version__}",
            output.getvalue()
        )

    def test_size_picker_heading_shows_version(self):
        with patch.object(
            cli,
            "_ask",
            return_value="0"
        ), redirect_stdout(
            io.StringIO()
        ) as output:
            selected = cli._pick(
                ["3x1", "4x6"],
                "LABEL SIZE",
                lambda x: x
            )

        self.assertIsNone(selected)
        self.assertIn(
            f"DriveBench v{__version__}",
            output.getvalue()
        )


if __name__ == "__main__":
    unittest.main()
