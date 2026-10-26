"""Mock-only tests. No mount, sudo, or block device operations occur."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drivebench import deps, mounts, store


class MountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.tmp.name) / "config"),
                                   "XDG_STATE_HOME": str(Path(self.tmp.name) / "state")})
        env.start()
        self.addCleanup(env.stop)
        store.initialize()

    def test_mountinfo_octal_escape(self):
        path = Path(self.tmp.name) / "mountinfo"
        path.write_text('100 1 8:17 / /mnt/disk\\040one ro,nosuid - ext4 /dev/sdb1 ro\n')
        got = mounts.live_mounts(path)
        self.assertEqual(got[0]["mountpoint"], "/mnt/disk one")
        self.assertEqual(got[0]["majmin"], "8:17")
        self.assertEqual(got[0]["mount_id"], "100")

    def test_ext4_ro_noload_and_xfs_norecovery(self):
        self.assertEqual(mounts._mount_options("ext4"), ["ro", "nosuid", "nodev", "noexec", "noload"])
        self.assertIn("norecovery", mounts._mount_options("xfs"))
        self.assertIn("uid=" + str(os.getuid()), mounts._mount_options("vfat"))
        with self.assertRaises(mounts.MountError):
            mounts._mount_options("ext4,exec")

    def test_mount_rejects_protected_and_excluded(self):
        for r in ("SYSTEM: root", "IGNORED: controller"):
            with self.assertRaises(mounts.MountError):
                mounts._allowed({"reasons": [r]})
        mounts._allowed({"reasons": ["IDENTITY UNVERIFIED"]})

    def test_previously_recorded_mount_not_active_after_power_cycle(self):
        store.record_mount("/mnt/drivebench/DB-000001-sdb1-001", "/dev/sdb1", "8:17", "DB-000001", "disk:foo", "ext4", "100", "boot-A")
        simulated_live = [{"mountpoint": "/mnt/drivebench/DB-000001-sdb1-001", "majmin": "8:17", "mount_id": "100", "source": "/dev/sdb1", "fstype": "ext4", "options": ["ro"]}]
        with patch.object(mounts, "live_mounts", return_value=simulated_live), patch.object(mounts, "boot_id", return_value="boot-B"):
            info = mounts.active_managed()[0]
        self.assertFalse(info["active"])
        self.assertEqual(info["status"], "PATH REUSED (not DriveBench's mount)")

    def test_live_mounted_status_and_unmounted_status(self):
        target = "/mnt/drivebench/DB-000001-sdb1-001"
        store.record_mount(target, "/dev/sdb1", "8:17", "DB-000001", "disk:foo", "ext4", "222", "boot-A")
        entry = {"mountpoint": target, "majmin": "8:17", "mount_id": "222", "source": "/dev/sdb1", "fstype": "ext4", "options": ["ro"]}
        with patch.object(mounts, "live_mounts", return_value=[entry]), patch.object(mounts, "boot_id", return_value="boot-A"):
            self.assertTrue(mounts.active_managed()[0]["active"])
        with patch.object(mounts, "live_mounts", return_value=[]):
            self.assertFalse(mounts.active_managed()[0]["active"])
        store.record_unmount(target)
        with patch.object(mounts, "live_mounts", return_value=[entry]), patch.object(mounts, "boot_id", return_value="boot-A"):
            self.assertFalse(mounts.active_managed()[0]["active"])

    def test_mount_command_is_ro_and_registry_written(self):
        disk = {"record_id": "DB-000001", "identity": "disk:one", "reasons": []}
        part = {"path": "/dev/sdb1", "type": "part", "fstype": "ext4", "majmin": "8:17"}
        target = Path("/mnt/drivebench/DB-000001-sdb1-001")
        mountinfo = [{"mountpoint": str(target), "majmin": "8:17", "mount_id": "101"}]
        with patch.object(mounts, "_block_majmin", return_value="8:17"), \
             patch.object(mounts, "_destination", return_value=target), \
             patch.object(mounts, "_ensure_dir"), \
             patch.object(mounts, "live_mounts", side_effect=[[], [], mountinfo]), \
             patch.object(mounts, "is_active", return_value=True), \
             patch.object(mounts, "boot_id", return_value="boot-C"), \
             patch.object(mounts, "_sudo") as sudo:
            result = mounts.mount_partition(disk, part, confirm=False)
        self.assertEqual(result, str(target))
        sudo.assert_called_once_with(["mount", "-t", "ext4", "-o", "ro,nosuid,nodev,noexec,noload", "--", "/dev/sdb1", str(target)])
        saved = store.read_state()["mounts"][str(target)]
        self.assertEqual(saved["boot_id"], "boot-C")

    def test_no_duplicate_mount(self):
        part = {"path": "/dev/sdb1", "type": "part", "fstype": "ext4", "majmin": "8:17"}
        with patch.object(mounts, "_block_majmin", return_value="8:17"), \
             patch.object(mounts, "live_mounts", return_value=[{"majmin": "8:17", "mountpoint": "/elsewhere"}]):
            with self.assertRaisesRegex(mounts.MountError, "already mounted"):
                mounts.mount_partition({"reasons": []}, part, confirm=False)

    def test_different_mount_names_for_multiple_entries(self):
        part = {"path": "/dev/sdb1"}
        disk = {"record_id": "DB-000007"}
        with patch.object(mounts, "live_mounts", return_value=[]):
            first = mounts._destination(disk, part)
            store.record_mount(str(first), "/dev/sdb1", "8:17", "DB-000007", "disk:X", "ext4", "1", "B")
            second = mounts._destination(disk, part)
        self.assertNotEqual(first, second)
        self.assertTrue(str(second).endswith("-002"))

    def test_missing_deps_shows_install_command(self):
        text = deps.instructions([("mount", "util-linux", True, "mount partitions"),
                                  ("lspci", "pciutils", False, "controller display")])
        self.assertIn("sudo apt install pciutils util-linux", text)


if __name__ == "__main__":
    unittest.main()
