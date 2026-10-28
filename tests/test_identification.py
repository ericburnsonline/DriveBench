"""Serial discovery, manual labels, and persistence after simulated reboot."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drivebench import inventory, store, processing
from drivebench import cli


def disk(*, path='/dev/sdb', identity=None, size=18_351_959_040, model='ST318406LW'):
    return dict(path=path, kname=Path(path).name, serial='', vendor='SEAGATE', model=model,
                size=size, diskseq='100', hctl='2:0:2:0', slot='scsi-slot:test:0:2:0',
                tran='scsi', identity=identity, confidence='strong' if identity else 'unverified',
                serial_source='unavailable', wwn='', controller_id='test')


class AutoSerialTests(unittest.TestCase):
    def setUp(self):
        inventory._SERIAL_CACHE.clear()

    def test_scsi_smartctl_serial_recognized_without_rc_zero(self):
        sample = dict(serial_number='3FE1MTKP00007310BJH2', device={'protocol':'SCSI'})
        result = subprocess.CompletedProcess(['smartctl'], 4, json.dumps(sample), '')
        with patch.object(inventory.shutil, 'which', return_value='/usr/bin/smartctl'), \
             patch.object(inventory.subprocess, 'run', return_value=result) as runner:
            number, source = inventory.probe_device_serial('/dev/sdb', bus='scsi', diskseq='100',
                                                            size=18, model='ST318406LW')
        self.assertEqual(number, sample['serial_number'])
        self.assertIn('smartctl', source)
        runner.assert_called_once()
        self.assertNotIn('sudo', runner.call_args.args[0])

    def test_usb_bridge_scsi_serial_is_not_trusted(self):
        fake = dict(serial_number='BRIDGE123', device={'protocol':'SCSI'})
        result = subprocess.CompletedProcess(['smartctl'], 0, json.dumps(fake), '')
        with patch.object(inventory.shutil, 'which', return_value='/usr/bin/smartctl'), \
             patch.object(inventory.subprocess, 'run', return_value=result):
            number, source = inventory.probe_device_serial('/dev/sdb', bus='usb')
        self.assertEqual(number, '')
        self.assertEqual(source, '')

    def test_native_usb_ata_passthrough_serial_is_trusted(self):
        fake = dict(serial_number='DISK123', device={'protocol':'ATA'})
        result = subprocess.CompletedProcess(['smartctl'], 0, json.dumps(fake), '')
        with patch.object(inventory.shutil, 'which', return_value='/usr/bin/smartctl'), \
             patch.object(inventory.subprocess, 'run', return_value=result) as runner:
            number, source = inventory.probe_device_serial('/dev/sdb', bus='usb')
        self.assertEqual(number, 'DISK123')
        self.assertIn('pass-through', source)
        self.assertIn('sat', runner.call_args.args[0])

    def test_native_scsi_vpd_fallback(self):
        smart = subprocess.CompletedProcess(['smartctl'], 2, '', 'permission denied')
        sg = subprocess.CompletedProcess(['sg_inq'], 0, 'Unit serial number: SN-00123\n', '')
        with patch.object(inventory.shutil, 'which', return_value='/usr/bin/test'), \
             patch.object(inventory.subprocess, 'run', side_effect=[smart, sg]):
            self.assertEqual(inventory.probe_device_serial('/dev/sdb', bus='scsi'),
                             ('SN-00123', 'SCSI VPD page 0x80'))


class ManualPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            'XDG_CONFIG_HOME': self.temp.name + '/cfg',
            'XDG_STATE_HOME': self.temp.name + '/state'})
        self.env.start()
        self.addCleanup(self.env.stop)
        store.initialize()
        cli._SESSION_MANUAL_LINKS.clear()
        self.addCleanup(cli._SESSION_MANUAL_LINKS.clear)

    def test_manual_labels_persist_but_do_not_auto_link_after_reboot(self):
        d = disk()
        recid = store.save_identification(d, record_id=None, manual_serial='PAPER-123', asset_id='MY-18GB')
        self.assertIn(recid, store.read_state()['records'])
        saved = store.read_state()['records'][recid]
        self.assertEqual(saved['manual_serial'], 'PAPER-123')
        self.assertEqual(saved['asset_id'], 'MY-18GB')
        self.assertIsNone(saved['identity'])
        self.assertEqual(store.remember_disks([disk(path='/dev/sde')]), {})
        self.assertEqual(store.confirm_manual_attachment(disk(path='/dev/sde'), recid)['id'], recid)
        store.touch_manual_attachment(disk(path='/dev/sde'), recid)
        self.assertEqual(store.read_state()['records'][recid]['last_device']['path'], '/dev/sde')

    def test_auto_verified_record_persists_operator_label(self):
        d = disk(identity='drive:verified_serial')
        rid = store.remember_disks([d])[d['path']]['id']
        store.save_identification(d, record_id=rid, manual_serial='STICKER123', asset_id='STOCK-08')
        rebooted = disk(path='/dev/sde', identity=d['identity'])
        self.assertEqual(store.remember_disks([rebooted])['/dev/sde']['id'], rid)
        self.assertEqual(store.read_state()['records'][rid]['asset_id'], 'STOCK-08')

    def test_duplicate_asset_id_rejected(self):
        store.save_identification(disk(), record_id=None, manual_serial='SN1', asset_id='BOX-1')
        with self.assertRaisesRegex(ValueError, 'already assigned'):
            store.save_identification(disk(path='/dev/sdc'), record_id=None,
                                      manual_serial='SN2', asset_id='box-1')
        self.assertEqual(len(store.read_state()['records']), 1)

    def test_relink_rejects_different_capacity(self):
        rid = store.save_identification(disk(), record_id=None, manual_serial='SN1', asset_id='BOX-1')
        with self.assertRaisesRegex(ValueError, 'Model or capacity'):
            store.confirm_manual_attachment(disk(size=5_000), rid)

    def test_manual_record_tracks_interrupted_and_completed_wipe(self):
        d = disk()
        rid = store.save_identification(d, record_id=None, manual_serial='SN1', asset_id='BOX-1')
        store.touch_manual_attachment(d, rid)
        d.update(manual_record_id=rid, operator_bound_session=True)
        operation = store.start_processing(d, 'wipe', {'passes_requested': 1})
        self.assertEqual(operation['record_id'], rid)
        self.assertEqual(store.read_state()['records'][rid]['status'], 'WIPE_IN_PROGRESS')
        # Simulated power loss: no finish call. Stored state must remain IN_PROGRESS.
        self.assertEqual(store.read_state()['records'][rid]['operations'][-1]['outcome'], 'IN_PROGRESS')
        store.finish_processing(operation, success=True, fields={'passes_completed': 1})
        self.assertEqual(store.read_state()['records'][rid]['status'], 'WIPED')

    def test_report_prints_both_serial_sources(self):
        d = disk(identity='drive:known')
        d['serial'] = 'FIRMWARE-123'
        rec = {'id':'DB-000001', 'asset_id':'BIN-01', 'manual_serial':'PAPER-SN'}
        report = processing._report_body(d, record=rec, operations=[])
        self.assertIn('Hardware serial: FIRMWARE-123', report)
        self.assertIn('Operator-supplied serial: PAPER-SN', report)
        self.assertIn('Asset ID: BIN-01', report)


if __name__ == '__main__':
    unittest.main()
