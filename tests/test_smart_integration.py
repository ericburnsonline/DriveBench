
"""Integration regression tests: SMART history persistence, menus, PDF sections.

All data simulated. No real block devices or destructive commands used.
"""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from drivebench import cli, diagnostics, reports, store


class SmartHistoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = Path(self.tmp.name) / 'cfg.json'
        state = Path(self.tmp.name) / 'state.json'
        pp = patch.object(store, 'paths', return_value=(config, state))
        pp.start()
        self.addCleanup(pp.stop)
        self.disk = {'path':'/dev/sdz','identity':'verified-serial', 'size':16000,
                     'model':'USB test', 'serial':'SERIAL-1', 'tran':'usb',
                     'vendor':'ACME', 'confidence':'strong'}
        ref = store.remember_disks([self.disk])
        self.disk['record_id'] = ref[self.disk['path']]['id']
        self.snapshot = {'schema_version':1, 'device_at_collection':'/dev/sdz',
                         'capacity_bytes':16000, 'drive_identity':'verified-serial',
                         'collection_status':'COLLECTED','collected_at':'2026-11-22T12:00Z',
                         'metrics':{'ata_pending_sectors':2},'serial':'SERIAL-1',
                         'model':'USB test','ata_attributes':[],'probe':'sat',
                         'smart_passed':True}

    def test_two_snapshots_persist_and_keep_disk_status(self):
        for hour in (12, 13):
            snap = dict(self.snapshot, collected_at=f'2026-11-22T{hour}:00Z')
            store.record_smart_snapshot(self.disk, snap)
        record = store.read_state()['records'][self.disk['record_id']]
        self.assertEqual(2, len(record['smart_history']))
        self.assertEqual('DISCOVERED', record['status'])
        self.assertEqual('2026-11-22T13:00Z',
                         store.smart_history(record['id'])[-1]['collected_at'])

    def test_mismatched_snapshot_rejected(self):
        for field, value in [('device_at_collection', '/dev/sdy'),
                             ('capacity_bytes', 90),
                             ('drive_identity', 'another'),
                             ('collection_status', 'BAD')]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    store.record_smart_snapshot(
                        self.disk, dict(self.snapshot, **{field:value}))
        self.assertEqual([], store.smart_history(self.disk['record_id']))

    def test_unlinked_disk_cannot_save(self):
        unknown = dict(self.disk, identity=None, record_id=None)
        with self.assertRaises(ValueError):
            store.record_smart_snapshot(
                unknown, dict(self.snapshot, drive_identity=None))

    def test_report_shows_saved_before_after_and_details(self):
        before = dict(self.snapshot, ata_attributes=[
            {'id':197, 'name':'Pending', 'raw':2,
             'normalized':100, 'worst':100, 'threshold':0}])
        after = dict(before, collected_at='2026-11-22T13:00Z',
                     metrics={'ata_pending_sectors':0})
        comparison = {
            'before_at':'2026-11-22T12:00Z',
            'after_at':'2026-11-22T13:00Z',
            'identity_confidence':'INVENTORY_HARDWARE_IDENTITY',
            'rows':[{'label':'Pending sectors',
                     'before':2, 'after':0, 'delta':-2}],
            'limitations':'Counter change is not proof of health'
        }
        record = {'id':self.disk['record_id'],
                  'smart_history':[before, after]}
        with patch.object(diagnostics, 'compare_smart_snapshots',
                          return_value=comparison):
            sections = reports.build_sections(self.disk, record)
        self.assertIn('SMART before / after comparison',
                      [s.title for s in sections])
        self.assertTrue(any('Change -2' in v
                            for s in sections for _, v in s.rows))
        self.assertIn('Latest ATA SMART attribute details',
                      [s.title for s in sections])

    def test_menu_saves_only_after_second_identity_check(self):
        reading = dict(self.snapshot)
        calls = []

        def save(*args):
            calls.append(args)
            return 'SMART-TEST'

        with patch.object(cli, '_ask', side_effect=['n', 'n']), \
             patch.object(cli, '_refresh_selected_disk',
                          return_value=self.disk), \
             patch.object(cli.processing, '_same', return_value=True), \
             patch.object(cli.diagnostics, 'collect_smart_snapshot',
                          return_value=reading), \
             patch.object(cli.diagnostics, 'SMART_METRIC_LABELS',
                          new={'ata_pending_sectors':'Pending'}), \
             patch.object(cli.store, 'record_smart_snapshot',
                          side_effect=save):
            cli._collect_smart_for_disk(self.disk)
        self.assertEqual(1, len(calls))


if __name__ == '__main__':
    unittest.main()
