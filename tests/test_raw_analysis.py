"""Mocked raw-media tests: never read from or write to real disk devices."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from drivebench import raw_analysis as raw, store, processing, cli


def sample_disk(**kwargs):
    d = {'path': '/dev/sdz', 'size': 8 * raw.MIB, 'identity': 'drive:known',
         'model': 'SCSI', 'serial': 'ABC', 'wwn': '', 'diskseq': '100', 'slot': 'slot1',
         'hctl': '2:0:2:0', 'tran': 'scsi', 'controller_id': 'controller1',
         'system_reasons': [], 'partitions': [], 'readonly': False}
    d.update(kwargs)
    return d


class SignatureTests(unittest.TestCase):
    def test_detects_mbr_and_gpt(self):
        b = bytearray(2048)
        b[510:512] = b'\x55\xaa'
        b[446 + 4] = 0x83
        b[446 + 12] = 1
        b[512:520] = b'EFI PART'
        markers = raw._structures(bytes(b))
        self.assertIn('Possible MBR partition entries', markers)
        self.assertIn('GPT header at LBA 1', markers)

    def test_empty_partition_table_not_evidence(self):
        b = bytearray(2048)
        b[510:512] = b'\x55\xaa'
        self.assertEqual(raw._structures(b), [])

    def test_legacy_apple_header(self):
        b = bytearray(2048)
        b[1024:1026] = b'H+'
        self.assertTrue(any('HFS' in x for x in raw._structures(b)))

    def test_file_signature_candidates_do_not_claim_recovery(self):
        evidence = raw.EvidenceCounter()
        evidence.feed(b'\x00' * 20 + b'%PDF-1.7' + b'\x00' * 15, 0, contiguous=False)
        r = evidence.result(10_000, 'quick', [[0, 43]], [], False)
        self.assertEqual(r['file_signature_candidates']['PDF'], 1)
        self.assertEqual(r['assessment'], 'INCONCLUSIVE_SAMPLED_ONLY' if not r['file_signature_candidates'] else 'EVIDENCE_DETECTED')
        self.assertFalse(r['fully_scanned'])
        self.assertIn('false positives', r['limitations'])

    def test_signatures_spanning_blocks_counted_once(self):
        c = raw.EvidenceCounter()
        c.feed(b'x' + b'\x89PNG\r', 0, contiguous=False)
        c.feed(b'\n\x1a\nyy', 6, contiguous=True)
        self.assertEqual(c.counts['PNG'], 1)
        self.assertEqual(c.first_offsets['PNG'], [1])

    def test_no_signatures_not_evidence_never_used(self):
        c = raw.EvidenceCounter()
        c.feed(b'\0' * 512, 0, contiguous=False)
        r = c.result(512, 'full', [[0, 512]], [], True)
        self.assertEqual(r['assessment'], 'NO_RECOGNIZABLE_EVIDENCE_DETECTED')
        self.assertAlmostEqual(r['zero_fraction_in_examined_bytes'], 1)

    def test_nonoverlapping_quick_ranges(self):
        parts = raw.sample_spans(20 * raw.MIB)
        self.assertEqual(parts[0][0], 0)
        self.assertEqual(parts[-1][0] + parts[-1][1], 20 * raw.MIB)
        for a,b in zip(parts, parts[1:]):
            self.assertLessEqual(a[0]+a[1], b[0])


class PersistentAnalysisTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {'XDG_CONFIG_HOME': tmp.name + '/cfg',
                                      'XDG_STATE_HOME': tmp.name + '/state'})
        env.start()
        self.addCleanup(env.stop)
        store.initialize()

    def test_scan_status_survives_reboot_simulation(self):
        disk = sample_disk()
        store.remember_disks([disk])
        ref = store.start_analysis(disk, 'quick_scan', {'disk_size_bytes': disk['size']})
        rid = ref['record_id']
        self.assertEqual(store.read_state()['records'][rid]['raw_analysis'][-1]['status'], 'IN_PROGRESS')
        store.finish_analysis(ref, report={'assessment': 'EVIDENCE_DETECTED'}, success=True)
        entry = store.read_state()['records'][rid]['raw_analysis'][-1]
        self.assertEqual(entry['result']['assessment'], 'EVIDENCE_DETECTED')
        self.assertEqual(entry['status'], 'COMPLETED')

    def test_unidentified_drive_history_not_attached_by_path(self):
        disk = sample_disk(identity=None)
        ref = store.start_analysis(disk, 'quick_scan')
        self.assertIsNone(ref['record_id'])
        self.assertIn(ref['event_id'], store.read_state()['unlinked_analysis'])
        self.assertEqual(raw.history_for(disk), [])

    def test_manual_session_history_requires_confirmed_binding(self):
        d = sample_disk(identity=None)
        rid = store.save_identification(d, record_id=None, manual_serial='LABEL123', asset_id='BOX1')
        d.update(manual_record_id=rid, operator_bound_session=True)
        store.touch_manual_attachment(d, rid)
        ref = store.start_analysis(d, 'full_scan')
        self.assertEqual(ref['record_id'], rid)
        self.assertEqual(store.read_state()['records'][rid]['raw_analysis'][0]['status'], 'IN_PROGRESS')

    def test_report_includes_saved_raw_scan_evidence(self):
        report = processing._report_body(sample_disk(), record={
            'id':'DB-000001', 'raw_analysis': [{'status': 'COMPLETED',
            'result':{'assessment':'EVIDENCE_DETECTED'}}]}, operations=[])
        self.assertIn('EVIDENCE_DETECTED', report)
        self.assertIn('raw_analysis', report)


_original_os_stat = os.stat


def fake_os_stat(path, *args, **kwargs):
    if str(path) == '/dev/sdz':
        class DeviceStats:
            st_rdev = os.makedev(8, 240)
        return DeviceStats()
    return _original_os_stat(path, *args, **kwargs)


class SafetyTests(unittest.TestCase):
    def test_disk_menu_can_reach_raw_analysis(self):
        d = sample_disk(reasons=[], record_id='DB-1', status='DISCOVERED')
        with patch.object(cli, '_ask', return_value='11'), \
             patch.object(cli, '_raw_analysis_menu') as menu:
            cli._disk_menu(d)
        menu.assert_called_once_with(d)

    def test_protected_system_drive_refused(self):
        d = sample_disk(system_reasons=['root filesystem'])
        with patch.object(raw.inventory, 'discover', return_value={'disks':[d], 'warnings':[]}), \
             patch.object(raw.processing, '_same', return_value=True), \
             patch.object(raw.store, 'read_config', return_value={}):
            with self.assertRaisesRegex(raw.AnalysisError, 'SYSTEM'):
                raw._source(d)

    def test_excluded_controller_refused(self):
        d = sample_disk()
        with patch.object(raw.inventory, 'discover', return_value={'disks':[d], 'warnings':[]}), \
             patch.object(raw.processing, '_same', return_value=True), \
             patch.object(raw.store, 'read_config', return_value={'ignored_controllers':['controller1']}):
            with self.assertRaisesRegex(raw.AnalysisError, 'Controller excluded'):
                raw._source(d)

    def test_changed_disk_refused(self):
        d = sample_disk()
        d2 = sample_disk(serial='DIFFERENT')
        with patch.object(raw.inventory, 'discover', return_value={'disks':[d2], 'warnings':[]}), \
             patch.object(raw.processing, '_same', return_value=False):
            with self.assertRaisesRegex(raw.AnalysisError, 'changed'):
                raw._source(d)

    def test_image_destination_same_disk_refused(self):
        d = sample_disk()
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(raw.shutil, 'which', return_value='/usr/bin/tool'), \
             patch.object(raw.subprocess, 'run') as run, \
             patch.object(raw.diagnostics, 'checked_device', side_effect=['/dev/sdz', '/dev/sdz1']), \
             patch.object(raw.os, 'stat', side_effect=lambda path, *a, **kw: fake_os_stat(path, *a, **kw)):

            run.side_effect = [type('P', (), {'returncode':0,'stdout':'/dev/sdz1\n'})(),
                               type('P', (), {'returncode':0,'stdout':'8:241\n8:240\n'})()]
            with self.assertRaisesRegex(raw.AnalysisError, 'source disk'):
                raw._destination_directory(d, tmp)

    def test_quick_scan_only_uses_read_only_dd(self):
        d = sample_disk(size=20 * raw.MIB)
        spans = raw.sample_spans(d['size'])
        data = [b'\0' * n for _, n in spans]
        first = bytearray(data[0])
        first[4096:4101] = b'%PDF-'
        data[0] = bytes(first)
        with patch.object(raw, '_source', return_value=d), \
             patch.object(raw, '_sudo_ready'), \
             patch.object(raw, '_ensure_dd'), \
             patch.object(raw, '_dd_read', side_effect=data) as reads, \
             patch.object(raw.store, 'start_analysis', return_value={'event_id':'S1'}) as started, \
             patch.object(raw.store, 'finish_analysis') as finished:
            report = raw.scan(d, full=False)
        self.assertEqual(len(spans), reads.call_count)
        self.assertEqual(report['file_signature_candidates']['PDF'], 1)
        self.assertEqual(report['assessment'], 'EVIDENCE_DETECTED')
        self.assertTrue(finished.call_args.kwargs['success'])
        started.assert_called_once()

    def test_full_scan_inherits_sudo_authentication_session(self):
        # The prior start_new_session=True caused sudo -n to be unable to
        # reuse sudo -v credentials on default tty/session-scoped policies.
        import io
        from unittest.mock import Mock
        proc = Mock()
        proc.stdout = io.BytesIO(b"\x00" * 4096)
        proc.wait.return_value = 0
        evidence = raw.EvidenceCounter()
        with patch.object(raw.subprocess, 'Popen', return_value=proc) as popen:
            rc, stderr, prefix = raw._run_full_scan('/dev/sdz', 4096, evidence)
        self.assertEqual(rc, 0)
        self.assertEqual(evidence.bytes_read, 4096)
        self.assertNotIn('start_new_session', popen.call_args.kwargs)
        self.assertIn('sudo', popen.call_args.args[0])
        self.assertIn('-n', popen.call_args.args[0])

    def test_interrupted_full_scan_terminates_only_reader(self):
        # Without a separate session, killpg(p.pid) could kill DriveBench
        # itself. Ensure interruption terminates only the spawned reader.
        from unittest.mock import Mock
        proc = Mock()
        proc.stdout.read.side_effect = KeyboardInterrupt()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        with patch.object(raw.subprocess, 'Popen', return_value=proc), \
             patch.object(raw.os, 'killpg') as killpg:
            with self.assertRaises(KeyboardInterrupt):
                raw._run_full_scan('/dev/sdz', 4096, raw.EvidenceCounter())
        proc.terminate.assert_called_once_with()
        killpg.assert_not_called()
        proc.wait.assert_called_once()

    def test_full_scan_failure_persisted_incomplete(self):
        d = sample_disk(size=20 * raw.MIB)
        with patch.object(raw, '_source', return_value=d), \
             patch.object(raw, '_sudo_ready'), \
             patch.object(raw, '_ensure_dd'), \
             patch.object(raw, '_run_full_scan', return_value=(1, 'read failure', b'\0'*2048)), \
             patch.object(raw.store, 'start_analysis', return_value={'event_id':'S1'}), \
             patch.object(raw.store, 'finish_analysis') as finished:
            with self.assertRaisesRegex(raw.AnalysisError, 'incomplete'):
                raw.scan(d, full=True)
        self.assertFalse(finished.call_args.kwargs['success'])

    def test_dd_commands_never_specify_output_device(self):
        import subprocess
        with patch.object(raw.subprocess, 'run', return_value=subprocess.CompletedProcess([],0,b'X'*16,b'')) as cmd:
            raw._dd_read('/dev/sdz', 0, 16)
        args = cmd.call_args.args[0]
        self.assertIn('if=/dev/sdz', args)
        self.assertFalse(any(x.startswith('of=') for x in args))
        self.assertNotIn('shell', cmd.call_args.kwargs)

    def test_testdisk_command_contains_no_write_action(self):
        d = sample_disk()
        with patch.object(raw, '_run_diagnostic_command', return_value={'exit_code':0}) as cmd:
            raw.search_partitions(d, deep=True)
            self.assertNotIn('write', ','.join(cmd.call_args.args[2]))


if __name__ == '__main__':
    unittest.main()
