
"""Read-only, simulated SMART snapshot and comparison regression tests.

Requires the structured SMART functions in the updated drivebench/diagnostics.py.
No real block devices or smartctl commands are accessed.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from drivebench import diagnostics


class SmartHistoryTests(unittest.TestCase):
    def setUp(self):
        self.disk = {'path': '/dev/sdz', 'identity': 'DB-HW-TEST',
                     'model': 'Test Disk', 'size': 2_000_000_000_000,
                     'tran': 'usb'}
        self.ata = {
            'device': {'protocol': 'ATA'},
            'model_name': 'Test Disk', 'serial_number': 'HW-SERIAL-123',
            'smart_status': {'passed': True},
            'power_on_time': {'hours': 22732},
            'temperature': {'current': 34},
            'ata_smart_attributes': {'table': [
                {'id': 5, 'name': 'Reallocated_Sector_Ct',
                 'raw': {'value': 0}, 'value': 100, 'worst': 100, 'thresh': 36},
                {'id': 197, 'name': 'Current_Pending_Sector',
                 'raw': {'value': 2}, 'value': 100, 'worst': 100, 'thresh': 0},
                {'id': 198, 'name': 'Offline_Uncorrectable', 'raw': {'value': 1}},
                {'id': 199, 'name': 'UDMA_CRC_Error_Count', 'raw': {'value': 4}},
            ]},
        }

    def snapshot(self, data=None, **kwargs):
        return diagnostics.smart_snapshot_from_json(
            self.ata if data is None else data, self.disk,
            captured_at='2026-11-22T10:00:00+00:00', **kwargs)

    def test_ata_snapshot_captures_counters_and_original_attributes(self):
        snap = self.snapshot()
        self.assertEqual('COLLECTED', snap['collection_status'])
        self.assertIs(snap['smart_passed'], True)
        self.assertEqual(2, snap['metrics']['ata_pending_sectors'])
        self.assertEqual(0, snap['metrics']['ata_reallocated_sectors'])
        self.assertEqual(4, snap['metrics']['ata_udma_crc_errors'])
        self.assertEqual(22732, snap['metrics']['power_on_hours'])
        self.assertEqual(36, snap['ata_attributes'][0]['threshold'])
        self.assertEqual('HW-SERIAL-123', snap['serial'])
        self.assertEqual('DB-HW-TEST', snap['drive_identity'])

    def test_vendor_attribute_names_do_not_override_numeric_ids(self):
        data = {'ata_smart_attributes': {'table': [
            {'id': 197, 'name': 'Vendor_Specific_Pending', 'raw': {'value': '3'}},
            {'id': 9, 'name': 'Hours', 'raw': {'value': 700}},
            {'id': 5, 'name': 'Bad counter', 'raw': {'value': 'unknown'}},
        ]}}
        metrics = self.snapshot(data)['metrics']
        self.assertEqual(3, metrics['ata_pending_sectors'])
        self.assertEqual(700, metrics['power_on_hours'])
        self.assertNotIn('ata_reallocated_sectors', metrics)

    def test_missing_smart_fields_are_not_assumed_zero_or_healthy(self):
        snap = self.snapshot({'model_name': 'Test Disk'})
        self.assertEqual('UNAVAILABLE', snap['collection_status'])
        self.assertIsNone(snap['smart_passed'])
        self.assertEqual({}, snap['metrics'])
        self.assertNotIn('ata_pending_sectors', snap['metrics'])

    def test_smart_failure_and_smartctl_error_bits_are_preserved(self):
        failing = self.snapshot({'smart_status': {'passed': False}}, exit_status=8)
        self.assertEqual('COLLECTED', failing['collection_status'])
        self.assertIs(failing['smart_passed'], False)
        self.assertEqual(8, failing['smartctl_exit_status'])
        partial = self.snapshot(self.ata, exit_status=2)
        self.assertEqual('PARTIAL', partial['collection_status'])
        self.assertTrue(any('command/device error' in x for x in partial['messages']))

    def test_scsi_counters_are_extracted(self):
        data = {'scsi_model_name': 'SCSI Test', 'scsi_grown_defect_list': 6,
                'scsi_error_counter_log': {
                    'read': {'total_uncorrected_errors': 1},
                    'write': {'total_uncorrected_errors': 0},
                    'verify': {'total_uncorrected_errors': 3}}}
        metrics = self.snapshot(data)['metrics']
        self.assertEqual(6, metrics['scsi_grown_defects'])
        self.assertEqual(1, metrics['scsi_read_uncorrected'])
        self.assertEqual(0, metrics['scsi_write_uncorrected'])
        self.assertEqual(3, metrics['scsi_verify_uncorrected'])

    def test_nvme_counters_are_extracted(self):
        data = {'nvme_smart_health_information_log': {
            'media_errors': 5, 'num_err_log_entries': 12,
            'unsafe_shutdowns': 3, 'percentage_used': 9,
            'power_on_hours': 4000}}
        metrics = self.snapshot(data)['metrics']
        self.assertEqual(5, metrics['nvme_media_errors'])
        self.assertEqual(12, metrics['nvme_error_log_entries'])
        self.assertEqual(3, metrics['nvme_unsafe_shutdowns'])
        self.assertEqual(9, metrics['nvme_percentage_used'])
        self.assertEqual(4000, metrics['power_on_hours'])

    def test_comparison_shows_increase_decrease_and_unavailable(self):
        before = self.snapshot()
        after = self.snapshot()
        after['metrics'] = dict(before['metrics'])
        after['metrics']['ata_pending_sectors'] = 0
        after['metrics']['ata_udma_crc_errors'] = 8
        del after['metrics']['ata_offline_uncorrectable']
        result = diagnostics.compare_smart_snapshots(before, after)
        rows = {row['metric']: row for row in result['rows']}
        self.assertEqual(-2, rows['ata_pending_sectors']['delta'])
        self.assertEqual('DECREASED', rows['ata_pending_sectors']['change'])
        self.assertEqual(4, rows['ata_udma_crc_errors']['delta'])
        self.assertEqual('INCREASED', rows['ata_udma_crc_errors']['change'])
        self.assertEqual('UNCHANGED', rows['ata_reallocated_sectors']['change'])
        self.assertIsNone(rows['ata_offline_uncorrectable']['delta'])
        self.assertEqual('NOT_COMPARABLE', rows['ata_offline_uncorrectable']['change'])

    def test_conflicting_identity_serial_model_and_capacity_are_rejected(self):
        before = self.snapshot()
        for field, value in [('drive_identity', 'DIFFERENT'),
                             ('serial', 'ANOTHER-SERIAL'),
                             ('model', 'Another Model'),
                             ('capacity_bytes', 1000)]:
            with self.subTest(field=field):
                after = self.snapshot()
                after[field] = value
                with self.assertRaises(diagnostics.DiagnosticError):
                    diagnostics.compare_smart_snapshots(before, after)

    def test_same_device_path_alone_does_not_authorize_comparison(self):
        before = self.snapshot({'power_on_time': {'hours': 1}})
        after = self.snapshot({'power_on_time': {'hours': 2}})
        for snap in (before, after):
            snap['drive_identity'] = None
            snap['serial'] = ''
        with self.assertRaisesRegex(diagnostics.DiagnosticError, 'matching'):
            diagnostics.compare_smart_snapshots(before, after)

    def test_unavailable_snapshot_cannot_be_compared(self):
        before = self.snapshot({'smart_status': {'passed': True}})
        after = self.snapshot({'model_name': 'Test Disk'})
        with self.assertRaises(diagnostics.DiagnosticError):
            diagnostics.compare_smart_snapshots(before, after)

    def test_usb_sat_fallback_if_auto_has_no_useful_counters(self):
        auto = {'model_name': 'Test Disk', 'serial_number': 'HW-SERIAL-123'}
        sat = self.ata
        with patch.object(diagnostics, 'checked_device', return_value='/dev/sdz'), \
             patch.object(diagnostics.shutil, 'which', return_value='/usr/sbin/smartctl'), \
             patch.object(diagnostics, 'command', side_effect=[
                 (0, json.dumps(auto), ''), (0, json.dumps(sat), '')]) as cmd:
            result = diagnostics.collect_smart_snapshot(self.disk)
        self.assertEqual('sat', result['probe'])
        self.assertEqual(2, result['metrics']['ata_pending_sectors'])
        self.assertEqual(2, cmd.call_count)
        self.assertIn('sat', cmd.call_args_list[1].args[0])

    def test_failed_smart_health_does_not_trigger_optimistic_sat_fallback(self):
        auto = {'smart_status': {'passed': False}}
        with patch.object(diagnostics, 'checked_device', return_value='/dev/sdz'), \
             patch.object(diagnostics.shutil, 'which', return_value='/usr/sbin/smartctl'), \
             patch.object(diagnostics, 'command', return_value=(8, json.dumps(auto), '')) as cmd:
            result = diagnostics.collect_smart_snapshot(self.disk)
        self.assertIs(result['smart_passed'], False)
        self.assertEqual('auto', result['probe'])
        self.assertEqual(1, cmd.call_count)

    def test_sat_command_error_falls_back_to_auto_result(self):
        auto = {'power_on_time': {'hours': 4}}
        with patch.object(diagnostics, 'checked_device', return_value='/dev/sdz'), \
             patch.object(diagnostics.shutil, 'which', return_value='/usr/sbin/smartctl'), \
             patch.object(diagnostics, 'command', side_effect=[
                 (0, json.dumps(auto), ''), (2, 'not json', 'SAT unavailable')]):
            result = diagnostics.collect_smart_snapshot(self.disk)
        self.assertEqual('auto', result['probe'])
        self.assertEqual(4, result['metrics']['power_on_hours'])

    def test_non_object_smart_payload_is_refused(self):
        with self.assertRaises(diagnostics.DiagnosticError):
            diagnostics.smart_snapshot_from_json([], self.disk)


if __name__ == '__main__':
    unittest.main()
