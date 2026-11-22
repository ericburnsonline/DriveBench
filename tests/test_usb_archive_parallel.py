
"""Offline regression tests for the standalone parallel USB archive utility.

All block-device identities are simulated. No sudo, physical USB access,
actual mounts, ddrescue, PhotoRec, or destructive commands are executed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from tools import usb_archive_recover as archive


class ParallelLockTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.lock_dir = Path(temp.name)
        # Production requires root-owned lock files. For an unprivileged unit
        # test, change only the ownership reported to this module's checks.
        real_fstat = os.fstat

        def simulated_fstat(fd):
            original = real_fstat(fd)
            return types.SimpleNamespace(st_mode=original.st_mode, st_uid=0)

        patch_dir = patch.object(archive, '_lock_directory', return_value=self.lock_dir)
        patch_stat = patch.object(archive.os, 'fstat', side_effect=simulated_fstat)
        patch_dir.start()
        patch_stat.start()
        self.addCleanup(patch_dir.stop)
        self.addCleanup(patch_stat.stop)

    def test_second_exclusive_lock_on_same_drive_is_rejected(self):
        key = archive._device_lock_name(os.makedev(8, 80))
        with archive._file_lock(key):
            with self.assertRaisesRegex(RuntimeError, 'already in use'):
                with archive._file_lock(key):
                    self.fail('A second exclusive drive lock was acquired')
        with archive._file_lock(key):
            pass  # Released after previous context exited.

    def test_exclusive_lock_is_visible_to_another_python_process(self):
        key = archive._device_lock_name(os.makedev(8, 81))
        path = self.lock_dir / key
        probe = """import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(17)
"""
        with archive._file_lock(key):
            result = subprocess.run([sys.executable, '-c', probe, str(path)],
                                    check=False, capture_output=True, text=True)
            self.assertEqual(17, result.returncode, result.stderr)
        result = subprocess.run([sys.executable, '-c', probe, str(path)],
                                check=False, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_different_drives_can_be_locked_at_same_time(self):
        left = archive._device_lock_name(os.makedev(8, 80))
        right = archive._device_lock_name(os.makedev(8, 96))
        with archive._file_lock(left):
            with archive._file_lock(right):
                self.assertTrue((self.lock_dir / left).exists())
                self.assertTrue((self.lock_dir / right).exists())

    def test_multiple_shared_destination_locks_are_allowed(self):
        key = archive._device_lock_name(os.makedev(8, 112))
        with archive._file_lock(key, shared=True):
            with archive._file_lock(key, shared=True):
                with self.assertRaisesRegex(RuntimeError, 'already in use'):
                    with archive._file_lock(key, shared=False):
                        self.fail('Exclusive drive lock bypassed shared destination')

    def test_exclusive_source_blocks_same_disk_as_destination(self):
        device = os.makedev(8, 112)
        key = archive._device_lock_name(device)
        selected = {'path': '/dev/sdz', 'devnum': device}
        with patch.object(archive, '_destination_disks', return_value=[key]), \
             patch.object(archive, 'verify_source', return_value={}):
            with archive.reserve_destination(Path('/dummy')):
                with self.assertRaisesRegex(RuntimeError, 'already in use'):
                    with archive.reserve_source(selected):
                        self.fail('Destination disk was selected as a source')

    def test_source_lock_is_released_when_identity_validation_fails(self):
        selected = {'path': '/dev/sdz', 'devnum': os.makedev(8, 128)}
        key = archive._device_lock_name(selected['devnum'])
        with patch.object(archive, 'verify_source', side_effect=RuntimeError('identity changed')):
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                with archive.reserve_source(selected):
                    self.fail('Invalid source identity was accepted')
        with archive._file_lock(key):
            pass

    def test_loop_allocation_is_serialized_and_released(self):
        with archive.reserve_loop_allocation():
            with self.assertRaisesRegex(RuntimeError, 'already in use'):
                with archive.reserve_loop_allocation():
                    self.fail('Second allocator entered its critical section')
        with archive.reserve_loop_allocation():
            pass

    def test_lock_file_is_rejected_if_not_root_owned(self):
        with patch.object(archive.os, 'fstat', return_value=types.SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600, st_uid=1234)):
            with self.assertRaisesRegex(RuntimeError, 'Unsafe USB archive lock file'):
                with archive._file_lock('disk-8-80.lock'):
                    self.fail('Unsafe lock ownership was ignored')


class ArchiveWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name)
        self.device = {'path': '/dev/sdz', 'size': 1024, 'serial': 'USB-123',
                       'model': 'Demo', 'devnum': os.makedev(8, 240), 'diskseq': '8'}

    def test_two_runs_create_separate_directories_and_reports(self):
        def fake_image(_source, image_path, _map_path):
            image_path.write_bytes(b'X' * 1024)
            return 1024

        successful_partition = [{'existing_copied_and_verified': True,
                                 'recovery_command_succeeded': True}]
        with patch.object(archive, 'verify_source', return_value=self.device), \
             patch.object(archive, 'destination_safe', return_value=('disk', '/mnt/backup', 'ext4')), \
             patch.object(archive, 'unmount_usb'), \
             patch.object(archive, 'mounts', return_value=[]), \
             patch.object(archive.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=1_000_000_000)), \
             patch('builtins.input', return_value='ARCHIVE /dev/sdz'), \
             patch.object(archive, 'image_disk', side_effect=fake_image), \
             patch.object(archive, 'digest_file', return_value='same-sha256'), \
             patch.object(archive, 'make_loop', return_value=('/dev/loop9', [{'path': '/dev/loop9p1'}])), \
             patch.object(archive, 'process_partitions', return_value=successful_partition), \
             patch.object(archive, 'run'), \
             patch.object(archive.os, 'sync'), \
             patch.object(archive, 'wipe_zero_and_verify') as wipe:
            first = archive._process_usb(self.device, self.device, self.output, 'auto', False)
            second = archive._process_usb(self.device, self.device, self.output, 'auto', False)
        self.assertNotEqual(first, second)
        self.assertIn('_sdz_USB-123', first.name)
        self.assertIn('_sdz_USB-123', second.name)
        self.assertEqual({'drive.img', 'report.json'},
                         {p.name for p in first.iterdir()})
        self.assertTrue(json.loads((first / 'report.json').read_text())['archive_verified'])
        self.assertTrue(json.loads((second / 'report.json').read_text())['archive_verified'])
        wipe.assert_not_called()

    def test_archive_only_rejects_wrong_confirmation_without_wiping(self):
        with patch.object(archive, 'verify_source', return_value=self.device), \
             patch.object(archive, 'destination_safe', return_value=('disk', '/mnt/backup', 'ext4')), \
             patch.object(archive, 'unmount_usb'), \
             patch.object(archive.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=1_000_000_000)), \
             patch('builtins.input', return_value='WIPE /dev/sdz'), \
             patch.object(archive, 'image_disk') as image:
            result = archive._process_usb(self.device, self.device, self.output, 'auto', False)
        self.assertIsNone(result)
        image.assert_not_called()
        self.assertEqual([], list(self.output.iterdir()))

    def test_destination_on_source_is_refused(self):
        selected = {'path': '/dev/sdz', 'children': [{'path': '/dev/sdz1'}]}
        with patch.object(archive, 'filesystem_at', return_value={
            'source': '/dev/sdz1', 'target': '/mnt/backup', 'fstype': 'ext4'}), \
             patch.object(archive, 'block_ancestors', return_value={'/dev/sdz', '/dev/sdz1'}):
            with self.assertRaisesRegex(RuntimeError, 'selected for wiping'):
                archive.destination_safe(self.output, selected)


if __name__ == '__main__':
    unittest.main()
