import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drivebench import inventory, store
from drivebench.cli import snapshot, status_reasons


def mkdisk(path, serial, root=False):
    size = 5_000_000_000
    major_base = "8:0" if path == "/dev/sda" else "8:16"
    major_part = "8:1" if path == "/dev/sda" else "8:17"
    return {
        "path": path, "kname": Path(path).name, "size": size,
        "model": "TestDrive", "vendor": "ACME", "serial": serial,
        "wwn": "", "tran": "scsi", "pttype": "dos", "readonly": False,
        "removable": False, "controller_id": None,
        "slot": "scsi-slot:some-controller:0:1:0", "confidence": "strong",
        "identity": f"drive:key:{serial}", "identity_reason": "device serial",
        "system_reasons": [], "mountpoints": ["/"] if root else [],
        "partitions": [], "tree": {"path": path, "type": "disk", "maj:min": major_base, "mountpoints": [],
                     "children": [{"type": "part", "path": path + "1", "maj:min": major_part, "mountpoints": ["/"] if root else []}]},
    }


class IdentityTests(unittest.TestCase):
    def test_scsi_identity_stable_across_device_name(self):
        a, confidence, _ = inventory.drive_identity({"model": "Disk", "serial": "S-123", "vendor": "IBM", "size": 42, "tran": "scsi"}, {})
        b, confidence2, _ = inventory.drive_identity({"model": "Disk", "serial": "S-123", "vendor": "IBM", "size": 42, "tran": "scsi"}, {})
        self.assertEqual(a, b)
        self.assertEqual(confidence, "strong")
        self.assertEqual(confidence2, "strong")

    def test_usb_bridge_serial_not_trusted(self):
        value, confidence, _ = inventory.drive_identity({"model": "Adapter", "serial": "BRIDGE-123", "vendor": "USB", "size": 42, "tran": "usb"}, {"ID_BUS": "usb"})
        self.assertIsNone(value)
        self.assertEqual(confidence, "unverified")

    def test_usb_sat_serial_may_be_used(self):
        value, confidence, _ = inventory.drive_identity({"model": "MyDrive", "serial": "BRIDGE-123", "vendor": "ATA", "size": 42, "tran": "usb"}, {"ID_BUS": "usb", "ID_ATA_SERIAL": "Genuine-456"})
        self.assertTrue(value.startswith("drive:"))
        self.assertEqual(confidence, "strong")

    def test_system_disk_protected_even_when_root_nested(self):
        root = mkdisk("/dev/sda", "OS", root=True)
        other = mkdisk("/dev/sdb", "DATA")
        with patch.object(inventory, "_root_majmin", return_value="8:1"):
            protected, warnings = inventory.system_protection([root, other])
        self.assertIn("/dev/sda", protected)
        self.assertNotIn("/dev/sdb", protected)
        self.assertFalse(warnings)

    def test_fail_closed_when_root_unresolved(self):
        disk = mkdisk("/dev/sdc", "ONE")
        with patch.object(inventory, "_root_majmin", return_value="999:999"):
            protected, warnings = inventory.system_protection([disk])
        self.assertIn("/dev/sdc", protected)
        self.assertTrue(warnings)

    def test_slot_exclusion_and_protected_root(self):
        disk = mkdisk("/dev/sdb", "AA", root=True)
        disk["system_reasons"] = ["root filesystem"]
        reasons = status_reasons(disk, {"ignored_drives": [], "ignored_slots": [disk["slot"]], "ignored_controllers": []})
        self.assertTrue(any("SYSTEM" in reason for reason in reasons))
        self.assertTrue(any("connection slot" in reason for reason in reasons))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env1 = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.tmp.name) / "cfg"),
                                             "XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        self.env1.start()
        self.addCleanup(self.env1.stop)
        store.initialize()

    def test_reboot_simulated_by_path_change_preserves_history(self):
        first = mkdisk("/dev/sdb", "DRIVE123")
        initial = store.remember_disks([first])["/dev/sdb"]
        store.record_manual_wipe(initial["id"], "zero", 1, "Outside tool")
        # A simulated second program invocation with a new Linux /dev name.
        second = mkdisk("/dev/sdf", "DRIVE123")
        again = store.remember_disks([second])["/dev/sdf"]
        self.assertEqual(initial["id"], again["id"])
        self.assertEqual(again["status"], "MANUAL_WIPE_REPORTED")
        self.assertEqual(store.read_state()["records"][initial["id"]]["operations"][0]["source"], "operator_only_not_verified_by_drivebench")

    def test_just_a_rescan_does_not_mark_a_disk_wiped(self):
        disk = mkdisk("/dev/sdc", "FRESH")
        x = store.remember_disks([disk])[disk["path"]]
        y = store.remember_disks([disk])[disk["path"]]
        self.assertEqual(x["status"], "DISCOVERED")
        self.assertEqual(y["status"], "DISCOVERED")

    def test_unverified_disk_is_not_attached_to_a_record(self):
        d = mkdisk("/dev/sdd", "UNKNOWN")
        d["identity"] = None
        d["confidence"] = "unverified"
        self.assertEqual(store.remember_disks([d]), {})
        self.assertEqual(store.read_state()["records"], {})

    def test_exclusions_persist(self):
        store.add_exclusion("ignored_controllers", "pci:03:abcd")
        self.assertIn("pci:03:abcd", store.read_config()["ignored_controllers"])
        self.assertTrue(store.remove_exclusion("ignored_controllers", "pci:03:abcd"))
        self.assertNotIn("pci:03:abcd", store.read_config()["ignored_controllers"])

    def test_status_snapshot_from_mock_discovery(self):
        disk = mkdisk("/dev/sdc", "SERIAL")
        fake = {"disks": [disk], "controllers": [], "warnings": []}
        with patch("drivebench.cli.inv.discover", return_value=fake):
            result = snapshot()
        self.assertEqual(result["disks"][0]["record_id"], "DB-000001")
        self.assertNotIn("tree", result["disks"][0])


if __name__ == "__main__":
    unittest.main()
