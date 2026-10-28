import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from drivebench import diagnostics, inventory, updates


class FilesystemTests(unittest.TestCase):
    def test_pttype_null_is_not_evidence_disk_blank(self):
        statement = diagnostics.interpret_pttype(None, 0)
        self.assertIn('does NOT establish', statement)
        self.assertIn('unsupported', statement)

    def test_whole_device_filesystem_without_table(self):
        text = diagnostics.interpret_pttype(None, 0, 'ext4')
        self.assertIn('ext4', text)

    def test_probe_commands_are_read_only_and_report_errors(self):
        with patch.object(diagnostics, 'checked_device', return_value='/dev/sdz'), \
             patch.object(diagnostics.shutil, 'which', return_value='/usr/bin/probe'), \
             patch.object(diagnostics, 'command', side_effect=[
                 (0, 'TYPE=ext4\nUUID=abc\n', ''),
                 (0, 'offset     type\n0x438      ext4\n', ''),
             ]) as run:
            text = diagnostics.filesystem_report({'path': '/dev/sdz', 'partitions': [], 'pttype': None})
        self.assertIn('TYPE: ext4', text)
        self.assertIn('no-act', str(run.call_args_list))
        self.assertNotIn('wiped', text.lower())

    def test_permission_problem_is_not_blank_report(self):
        with patch.object(diagnostics, 'checked_device', return_value='/dev/sdz'), \
             patch.object(diagnostics.shutil, 'which', return_value='/usr/bin/probe'), \
             patch.object(diagnostics, 'command', side_effect=[
                 (2, '', 'permission denied'), (1, '', 'permission denied')
             ]):
            txt = diagnostics.filesystem_report({'path': '/dev/sdz', 'partitions': [], 'pttype': None})
        self.assertIn('inconclusive', txt)
        self.assertIn('permission denied', txt)


class ScsiWarnings(unittest.TestCase):
    def test_fifteen_scsi_targets_trigger_warning(self):
        disks = [dict(scsi_host='host2', model='ST318406LW', size=18000000000,
                      hctl=f'2:0:{t}:0') for t in range(1, 16)]
        warnings = inventory.scsi_alias_warnings(disks)
        self.assertEqual(len(warnings), 1)
        self.assertIn('Possible drive/controller SCSI ID conflict', warnings[0])

    def test_two_identical_drives_do_not_trigger(self):
        disks = [dict(scsi_host='host2', model='ST318406LW', size=18000000000,
                      hctl=f'2:0:{t}:0') for t in (1, 2)]
        self.assertEqual([], inventory.scsi_alias_warnings(disks))


class GithubUpdateTests(unittest.TestCase):
    def test_branch_listing_and_sha_validation(self):
        example = [{'name': 'main', 'commit': {'sha': 'a'*40}},
                   {'name': 'feature/probe', 'commit': {'sha': 'b'*40}},
                   {'name': 'bad', 'commit': {'sha': 'not-sha'}}]
        with patch.object(updates, '_get_json', return_value=example):
            branches = updates.branches()
        self.assertEqual([x['name'] for x in branches], ['main', 'feature/probe'])

    def test_download_validates_sha_before_wget(self):
        with self.assertRaises(updates.UpdateError):
            updates.download('../evil', 'main')

    def test_staging_never_replaces_project_files(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            existing = base/'installed.py'
            existing.write_text('original')
            fake = base/'source.zip'
            with zipfile.ZipFile(fake, 'w') as z:
                z.writestr('eric-DriveBench-deadbeef/drivebench/cli.py', 'print("new")')
            def mocked_run(argv, **kwargs):
                Path(argv[argv.index('-O')+1]).write_bytes(fake.read_bytes())
                return subprocess.CompletedProcess(argv, 0, '', '')
            with patch.object(updates.shutil, 'which', return_value='/usr/bin/wget'), \
                 patch.object(updates.subprocess, 'run', side_effect=mocked_run):
                result = updates.download('a'*40, 'fix/drive', base=base)
            self.assertTrue(result.is_file())
            self.assertEqual(existing.read_text(), 'original')
            self.assertIn('fix_drive', result.name)

    def test_download_requires_wget(self):
        with patch.object(updates.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(updates.UpdateError, 'sudo apt install wget'):
                updates.download('a'*40, 'main')


class HealthTests(unittest.TestCase):
    def test_smart_summary_reports_scsi_and_power_hours(self):
        data = {'scsi_model_name': 'ST318406LW', 'power_on_time': {'hours': 12345},
                'smart_status': {'passed': True}, 'scsi_grown_defect_list': 2}
        out = diagnostics.summarize_smart(data)
        self.assertIn('12345', out)
        self.assertIn('SCSI grown defects: 2', out)


if __name__ == '__main__':
    unittest.main()

class CompareFilesTests(unittest.TestCase):
    def test_changed_files_reports_only_differences(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'drivebench').mkdir()
            (root / 'drivebench/cli.py').write_text('old')
            (root / 'README.md').write_text('same')
            zipped = root / 'up.zip'
            with zipfile.ZipFile(zipped, 'w') as z:
                z.writestr('owner-DriveBench-sha/drivebench/cli.py', 'new')
                z.writestr('owner-DriveBench-sha/README.md', 'same')
                z.writestr('owner-DriveBench-sha/drivebench/updates.py', 'new file')
            changes = updates.changed_files(zipped, installed_root=root)
            self.assertEqual(changes['modified'], ['drivebench/cli.py'])
            self.assertEqual(changes['added'], ['drivebench/updates.py'])
            self.assertEqual(changes['unchanged'], ['README.md'])
