"""Safety-critical mock tests; NEVER operate on a real disk."""
import unittest
from unittest.mock import patch
from drivebench import processing, store


def fake_disk(**kw):
    base = {'path': '/dev/sdz', 'kname': 'sdz', 'majmin': '8:240',
            'size': 1000000000, 'model': 'SCSI HDD', 'serial': 'ABC123',
            'wwn': '', 'identity': 'drive:trustworthy', 'slot': 'scsi:1',
            'hctl': '1:0:2:0', 'controller_id': 'ctrlA', 'rotational': True,
            'system_reasons': [], 'readonly': False, 'tran': 'SCSI',
            'partitions': [], 'tree': {'type': 'disk', 'kname': 'sdz',
                                     'path': '/dev/sdz', 'maj:min': '8:240',
                                     'ro': 0, 'mountpoints': []}}
    base.update(kw)
    return base


class PreflightSafetyTests(unittest.TestCase):
    def check(self, disk):
        with patch.object(processing.inventory, 'discover', return_value={'disks': [disk], 'warnings': []}), \
             patch.object(processing.store, 'read_config', return_value={'ignored_drives': [], 'ignored_slots': [], 'ignored_controllers': []}), \
             patch.object(processing, '_majmin', return_value='8:240'), \
             patch.object(processing, '_swap_and_holders', return_value=[]), \
             patch.object(processing.storage_claims, 'inspect_claims', return_value={'device': disk['path'], 'holders': [], 'mounts': [], 'swaps': [], 'errors': [], 'blocked': False}), \
             patch.object(processing.mounts, 'live_mounts', return_value=[]):
            return processing.preflight(disk)

    def test_system_disk_denied(self):
        with self.assertRaisesRegex(processing.ProcessingError, 'SYSTEM'):
            self.check(fake_disk(system_reasons=['root filesystem']))

    def test_unknown_rotational_ssd_denied(self):
        with self.assertRaisesRegex(processing.ProcessingError, 'SSD'):
            self.check(fake_disk(rotational=False))

    def test_mounted_partition_denied(self):
        d = fake_disk()
        d['tree']['children'] = [{'type': 'part', 'path': '/dev/sdz1', 'maj:min': '8:241',
                                  'mountpoints': ['/data'], 'ro': 0, 'kname': 'sdz1'}]
        with self.assertRaisesRegex(processing.ProcessingError, 'mounted'):
            self.check(d)

    def test_multipath_holder_preflight_refuses_with_mapping_name(self):
        disk = fake_disk()
        report = {'device': '/dev/sdz', 'blocked': True,
                  'holders': [{'source': '/dev/sdz', 'device': '/dev/dm-0',
                               'kind': 'multipath', 'mapping_name': 'mpatha',
                               'mapping_uuid': 'mpath-WD', 'slaves': ['/dev/sdz']}],
                  'mounts': [], 'swaps': [], 'errors': []}
        with patch.object(processing.inventory, 'discover', return_value={'disks': [disk], 'warnings': []}), \
             patch.object(processing.store, 'read_config', return_value={}), \
             patch.object(processing, '_swap_and_holders', return_value=[]), \
             patch.object(processing, '_majmin', return_value='8:240'), \
             patch.object(processing.mounts, 'live_mounts', return_value=[]), \
             patch.object(processing.storage_claims, 'inspect_claims', return_value=report):
            with self.assertRaisesRegex(processing.ProcessingError, 'mpatha'):
                processing.preflight(disk)

    def test_failed_claim_inspection_cannot_authorize_wiping(self):
        disk = fake_disk()
        with patch.object(processing.inventory, 'discover', return_value={'disks': [disk], 'warnings': []}), \
             patch.object(processing.store, 'read_config', return_value={}), \
             patch.object(processing, '_swap_and_holders', return_value=[]), \
             patch.object(processing, '_majmin', return_value='8:240'), \
             patch.object(processing.mounts, 'live_mounts', return_value=[]), \
             patch.object(processing.storage_claims, 'inspect_claims', side_effect=OSError('sysfs unavailable')):
            with self.assertRaisesRegex(processing.ProcessingError, 'sysfs unavailable'):
                processing.preflight(disk)

    def test_valid_disk_check(self):
        self.assertEqual('/dev/sdz', self.check(fake_disk())['path'])

    def test_excluded_controller(self):
        d = fake_disk()
        with patch.object(processing.inventory, 'discover', return_value={'disks': [d], 'warnings': []}), \
             patch.object(processing.store, 'read_config', return_value={'ignored_drives': [], 'ignored_slots': [], 'ignored_controllers': ['ctrlA']}):
            with self.assertRaisesRegex(processing.ProcessingError, 'Controller excluded'):
                processing.preflight(d)


class ProcessingTests(unittest.TestCase):
    def test_rejects_invalid_pass_count_without_writing(self):
        with patch.object(processing, 'run_privileged') as run:
            with self.assertRaises(processing.ProcessingError):
                processing.wipe(fake_disk(), 'zero', 0, True)
            run.assert_not_called()

    def test_cancelled_wipe_does_not_write(self):
        d = fake_disk()
        with patch.object(processing, 'preflight', return_value=d), \
             patch.object(processing, 'confirm_action', return_value=False), \
             patch.object(processing, 'run_privileged') as run:
            with self.assertRaisesRegex(processing.ProcessingError, 'Cancelled'):
                processing.wipe(d, 'zero', 1, True)
            run.assert_not_called()

    def test_wipe_marks_success_only_after_pass_and_verification(self):
        d = fake_disk()
        with patch.object(processing, 'preflight', return_value=d), \
             patch.object(processing, 'confirm_action', return_value=True), \
             patch.object(processing, '_capture_diagnostics', return_value={}), \
             patch.object(processing.store, 'start_processing', return_value={'event_id':'OP-X'}) as begin, \
             patch.object(processing.store, 'update_processing') as update, \
             patch.object(processing.store, 'finish_processing') as finish, \
             patch.object(processing, 'run_privileged') as run:
            processing.wipe(d, 'zero', 2, True)
            self.assertEqual(3, run.call_count)
            self.assertEqual([1, 2], [x.args[1]['passes_completed'] for x in update.call_args_list])
            self.assertEqual('FULL ZERO READBACK MATCHED', finish.call_args.kwargs['fields']['verification'])
            self.assertTrue(finish.call_args.kwargs['success'])
            begin.assert_called_once()

    def test_failed_wipe_is_not_marked_complete(self):
        d = fake_disk()
        with patch.object(processing, 'preflight', return_value=d), \
             patch.object(processing, 'confirm_action', return_value=True), \
             patch.object(processing, '_capture_diagnostics', return_value={}), \
             patch.object(processing.store, 'start_processing', return_value={'event_id':'OP-X'}), \
             patch.object(processing.store, 'finish_processing') as finish, \
             patch.object(processing, 'run_privileged', side_effect=processing.ProcessingError('write error')):
            with self.assertRaises(processing.ProcessingError):
                processing.wipe(d, 'zero', 1, False)
            self.assertFalse(finish.call_args.kwargs['success'])

    def test_udev_settle_failure_fails_closed(self):
        import subprocess
        with patch.object(processing.shutil, 'which', return_value='/usr/bin/udevadm'), \
             patch.object(processing.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(processing.ProcessingError, 'udevadm settle failed'):
                processing._settle_storage_events('after overwrite')

    def test_holder_claim_after_overwrite_stops_before_readback(self):
        d = fake_disk()
        check_count = 0
        def staged_preflight(original):
            nonlocal check_count
            check_count += 1
            # Initial, confirmed and pre-pass checks pass; post-pass fails.
            if check_count == 4:
                raise processing.ProcessingError('HOLDER: /dev/sdz -> /dev/dm-0 (mpatha)')
            return d
        with patch.object(processing, 'preflight', side_effect=staged_preflight), \
             patch.object(processing, '_settle_storage_events'), \
             patch.object(processing, 'confirm_action', return_value=True), \
             patch.object(processing, '_capture_diagnostics', return_value={}), \
             patch.object(processing.store, 'start_processing', return_value={'event_id': 'OP-X'}), \
             patch.object(processing.store, 'update_processing') as update, \
             patch.object(processing.store, 'finish_processing') as finish, \
             patch.object(processing, 'run_privileged') as run:
            with self.assertRaisesRegex(processing.ProcessingError, 'mpatha'):
                processing.wipe(d, 'zero', 1, True)
            self.assertEqual(1, run.call_count)  # dd completed; cmp never started
            self.assertEqual(1, update.call_args.args[1]['passes_completed'])
            self.assertFalse(finish.call_args.kwargs['success'])
            self.assertEqual('after overwrite pass 1', finish.call_args.kwargs['fields']['failure_stage'])
            self.assertEqual('NOT COMPLETED', finish.call_args.kwargs['fields']['verification'])

    def test_post_overwrite_check_runs_even_without_verification(self):
        d = fake_disk()
        with patch.object(processing, 'preflight', side_effect=[d, d, d, processing.ProcessingError('mount appeared')]), \
             patch.object(processing, '_settle_storage_events'), \
             patch.object(processing, 'confirm_action', return_value=True), \
             patch.object(processing, '_capture_diagnostics', return_value={}), \
             patch.object(processing.store, 'start_processing', return_value={'event_id': 'OP-X'}), \
             patch.object(processing.store, 'update_processing'), \
             patch.object(processing.store, 'finish_processing') as finish, \
             patch.object(processing, 'run_privileged') as run:
            with self.assertRaisesRegex(processing.ProcessingError, 'mount appeared'):
                processing.wipe(d, 'zero', 1, False)
            self.assertEqual(1, run.call_count)
            self.assertFalse(finish.call_args.kwargs['success'])
            self.assertEqual('NOT REQUESTED', finish.call_args.kwargs['fields']['verification'])

    def test_format_refuses_when_cancelled(self):
        d = fake_disk()
        with patch.object(processing, 'preflight', return_value=d), \
             patch.object(processing.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(processing, 'confirm_action', return_value=False), \
             patch.object(processing, 'run_privileged') as run:
            with self.assertRaisesRegex(processing.ProcessingError, 'Cancelled'):
                processing.format_drive(d, 'fat32')
            run.assert_not_called()

    def test_format_exfat_gpt_marks_microsoft_basic_data(self):
        d = fake_disk()
        p = {'path': '/dev/sdz1', 'type': 'part', 'fstype': 'exfat'}
        after = fake_disk(partitions=[p])
        with patch.object(processing, 'preflight', side_effect=lambda x: x), \
             patch.object(processing.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(processing, 'confirm_action', return_value=True), \
             patch.object(processing.inventory, 'discover', return_value={'disks': [after], 'warnings': []}), \
             patch.object(processing, '_majmin', return_value='8:241'), \
             patch.object(processing.store, 'start_processing', return_value={'event_id': 'OP-X'}), \
             patch.object(processing.store, 'finish_processing') as finish, \
             patch.object(processing, '_settle_storage_events'), \
             patch.object(processing, 'run_privileged') as run:
            processing.format_drive(d, 'exfat')
        self.assertIn('msftdata', run.call_args_list[0].args[0])
        self.assertEqual('mkfs.exfat', run.call_args_list[-1].args[0][0])
        self.assertTrue(finish.call_args.kwargs['success'])

    def test_format_claim_after_filesystem_marks_incomplete(self):
        d = fake_disk()
        part = {'path': '/dev/sdz1', 'type': 'part', 'fstype': 'exfat'}
        after = fake_disk(partitions=[part])
        preflight_calls = 0
        def staged_check(disk):
            nonlocal preflight_calls
            preflight_calls += 1
            if preflight_calls == 4:
                raise processing.ProcessingError('MOUNTED: /dev/sdz1 at /media/user/DRIVEBENCH')
            return disk
        with patch.object(processing, 'preflight', side_effect=staged_check), \
             patch.object(processing, '_settle_storage_events'), \
             patch.object(processing.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(processing, 'confirm_action', return_value=True), \
             patch.object(processing.inventory, 'discover', return_value={'disks': [after]}), \
             patch.object(processing, '_majmin', return_value='8:241'), \
             patch.object(processing.store, 'start_processing', return_value={'event_id': 'OP-X'}), \
             patch.object(processing.store, 'finish_processing') as finish, \
             patch.object(processing, 'run_privileged') as run:
            with self.assertRaisesRegex(processing.ProcessingError, 'MOUNTED'):
                processing.format_drive(d, 'exfat')
            self.assertEqual('mkfs.exfat', run.call_args.args[0][0])
            self.assertFalse(finish.call_args.kwargs['success'])
            self.assertEqual('after filesystem creation', finish.call_args.kwargs['fields']['failure_stage'])

    def test_write_report_rejects_unverified_preexisting_partition(self):
        d = fake_disk(partitions=[{'path': '/dev/sdz1', 'type': 'part', 'fstype': 'vfat',
                                  'mountpoints': [], 'majmin': '8:241'}])
        with patch.object(processing, 'preflight', return_value=d), \
             patch.object(processing, '_majmin', return_value='8:241'), \
             patch.object(processing.store, 'read_state', return_value={'records': {}}), \
             patch.object(processing, 'run_privileged') as run:
            with self.assertRaisesRegex(processing.ProcessingError, 'No matching completed'):
                processing.write_report(d)
            run.assert_not_called()

    def test_report_has_wipe_provenance(self):
        out = processing._report_body(fake_disk(), record=None, operations=[
            {'operation': 'wipe', 'outcome': 'COMPLETED', 'method': 'zero',
             'passes_completed': 1, 'verification': 'FULL ZERO READBACK MATCHED',
             'finished_at': '2026-10-26T22:00:00+00:00'}])
        self.assertIn('2026-10-26T22:00:00+00:00', out)
        self.assertIn('FULL ZERO READBACK MATCHED', out)

    def test_no_wipe_does_not_claim_completed(self):
        out = processing._report_body(fake_disk(), record=None, operations=[])
        self.assertIn('NO VERIFIED HISTORICAL WIPE', out)


if __name__ == '__main__':
    unittest.main()
