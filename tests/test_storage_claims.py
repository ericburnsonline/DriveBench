
"""Read-only simulated sysfs tests: never access or change real drives."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from drivebench import storage_claims


class StorageClaimsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.sys_block = root / 'sys' / 'class' / 'block'
        self.mountinfo = root / 'mountinfo'
        self.swaps = root / 'swaps'
        self.sys_block.mkdir(parents=True)
        self.mountinfo.write_text('')
        self.swaps.write_text('Filename\tType\tSize\tUsed\tPriority\n')
        self.disk = {
            'path': '/dev/sdz', 'kname': 'sdz', 'majmin': '8:240',
            'diskseq': '12',
            'tree': {
                'type': 'disk', 'path': '/dev/sdz', 'kname': 'sdz',
                'maj:min': '8:240', 'children': [
                    {'type': 'part', 'path': '/dev/sdz1', 'kname': 'sdz1',
                     'maj:min': '8:241'},
                ],
            },
        }
        self.node('sdz', '8:240', diskseq='12')
        self.node('sdz1', '8:241')

    def node(self, name: str, majmin: str, *, diskseq: str = '') -> Path:
        root = self.sys_block / name
        (root / 'holders').mkdir(parents=True)
        (root / 'slaves').mkdir()
        (root / 'dev').write_text(majmin)
        if diskseq:
            (root / 'diskseq').write_text(diskseq)
        return root

    def inspect(self):
        return storage_claims.inspect_claims(
            self.disk, sys_block=self.sys_block,
            mountinfo=self.mountinfo, swaps=self.swaps,
        )

    def test_unclaimed_disk_reports_no_claim_and_no_authorization(self):
        result = self.inspect()
        self.assertFalse(result['blocked'])
        self.assertEqual([], result['holders'])
        self.assertIn('does NOT reserve or authorize', storage_claims.describe_claims(result))

    def test_multipath_holder_detected_on_disk(self):
        dm = self.node('dm-0', '253:0')
        (dm / 'dm').mkdir()
        (dm / 'dm' / 'name').write_text('mpatha')
        (dm / 'dm' / 'uuid').write_text('mpath-test-wwid')
        (dm / 'slaves' / 'sdz').touch()
        (self.sys_block / 'sdz' / 'holders' / 'dm-0').touch()

        result = self.inspect()
        self.assertTrue(result['blocked'])
        self.assertEqual('multipath', result['holders'][0]['kind'])
        self.assertEqual('mpatha', result['holders'][0]['mapping_name'])
        self.assertEqual(['/dev/sdz'], result['holders'][0]['slaves'])
        self.assertIn('/dev/sdz -> /dev/dm-0 (mpatha)', storage_claims.describe_claims(result))

    def test_partition_holder_and_nested_holder_detected(self):
        dm = self.node('dm-0', '253:0')
        (dm / 'dm').mkdir()
        (dm / 'dm' / 'name').write_text('crypt-data')
        self.node('dm-1', '253:1')
        (self.sys_block / 'sdz1' / 'holders' / 'dm-0').touch()
        (dm / 'holders' / 'dm-1').touch()

        result = self.inspect()
        self.assertTrue(result['blocked'])
        self.assertEqual(2, len(result['holders']))
        self.assertEqual({'/dev/sdz1', '/dev/dm-0'}, {h['source'] for h in result['holders']})
        self.assertTrue(all(h['kind'] == 'device-mapper' for h in result['holders']))

    def test_mount_matches_major_minor_not_mount_source_path(self):
        self.mountinfo.write_text(
            '81 1 8:241 / /mnt/usb rw,relatime - vfat /dev/disk/by-uuid/TEST rw\n'
        )
        result = self.inspect()
        self.assertTrue(result['blocked'])
        self.assertEqual('/dev/sdz1', result['mounts'][0]['device'])
        self.assertEqual('/mnt/usb', result['mounts'][0]['mountpoint'])

    def test_swap_on_target_partition_detected(self):
        self.swaps.write_text('Filename Type Size Used Priority\n/dev/sdz1 partition 1024 0 -2\n')
        result = self.inspect()
        self.assertTrue(result['blocked'])
        self.assertEqual('/dev/sdz1', result['swaps'][0]['device'])

    def test_incomplete_sysfs_inspection_fails_closed(self):
        (self.sys_block / 'sdz1' / 'dev').unlink()
        result = self.inspect()
        self.assertTrue(result['blocked'])
        self.assertTrue(any('sdz1' in error for error in result['errors']))

    def test_changed_device_identity_fails_closed(self):
        (self.sys_block / 'sdz' / 'dev').write_text('8:222')
        result = self.inspect()
        self.assertTrue(result['blocked'])
        self.assertTrue(any('number changed' in error for error in result['errors']))

    def test_changed_disk_sequence_fails_closed(self):
        (self.sys_block / 'sdz' / 'diskseq').write_text('13')
        result = self.inspect()
        self.assertTrue(result['blocked'])
        self.assertTrue(any('disk sequence changed' in error for error in result['errors']))

    def test_invalid_device_path_is_rejected(self):
        self.disk['kname'] = '../sda'
        result = self.inspect()
        self.assertTrue(result['blocked'])
        self.assertTrue(result['errors'])


if __name__ == '__main__':
    unittest.main()
