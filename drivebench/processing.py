"""Explicit, supervised destructive disk operations for DriveBench.

Linux-only. There is intentionally no unattended CLI entry point. Destructive
operations require a fresh inventory check and a second typed confirmation.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import diagnostics, inventory, mounts, storage_claims, store


class ProcessingError(RuntimeError):
    pass


# Preselected values, never user-generated command names.
FORMATTERS = {'fat32': 'mkfs.fat', 'exfat': 'mkfs.exfat'}
MAX_PASSES = 10
MIN_DISK_SIZE = 64 * 1024 * 1024
MAX_MBR_SIZE = 2 * 1000**4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def run_privileged(argv: list[str], *, check: bool = True) -> int:
    """Escalate only the chosen system utility. No shell/eval."""
    if not shutil.which('sudo'):
        raise ProcessingError('sudo missing: ask the administrator to install sudo.')
    print('  Running:', 'sudo', ' '.join(argv), flush=True)
    try:
        rc = subprocess.run(['sudo', '--', *argv], check=False).returncode
    except OSError as exc:
        raise ProcessingError(f'Cannot invoke sudo: {exc}') from exc
    if check and rc:
        raise ProcessingError(f'{argv[0]} failed (exit {rc}). Device may be partially modified.')
    return rc


def _majmin(device: str) -> str:
    info = os.stat(device)
    if not stat.S_ISBLK(info.st_mode):
        raise ProcessingError(f'Not a block device: {device}')
    return f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'


def _disk_tree_has_unsafe_nodes(disk: dict) -> list[str]:
    all_nodes = list(inventory.flatten(disk['tree']))
    errors = []
    for node in all_nodes:
        if inventory.mounted_at(node):
            errors.append('a filesystem is mounted')
        if node.get('type') not in ('disk', 'part'):
            errors.append(f"has mapped/complex storage node: {node.get('type')}")
        if str(node.get('ro', 0)) not in ('0', 'False', 'None'):
            errors.append('a device is read-only')
    return errors


def _swap_and_holders(disk: dict) -> list[str]:
    issues = []
    try:
        swap_paths = {os.path.realpath(line.split()[0]) for line in Path('/proc/swaps').read_text().splitlines()[1:] if line.strip()}
        for node in inventory.flatten(disk['tree']):
            if os.path.realpath(str(node.get('path') or '')) in swap_paths:
                issues.append('disk or partition is active swap')
    except OSError:
        issues.append('cannot inspect active swap')
    for node in inventory.flatten(disk['tree']):
        name = Path(str(node.get('kname') or node.get('path') or '')).name
        holders = Path('/sys/class/block') / name / 'holders'
        if not holders.is_dir():
            issues.append(f'cannot inspect holders for {name}')
        elif any(holders.iterdir()):
            issues.append(f'{name} has device-mapper/RAID holders')
    return issues


def _settle_storage_events(stage: str) -> None:
    """Wait for queued kernel/udev work; never claim this reserves the disk.

    A nonzero exit status fails closed. This does not prevent a service from
    acting later, so a new preflight inspection is always required afterward.
    """
    if not shutil.which('udevadm'):
        print('  udevadm unavailable; continuing with fresh drive-identity and claim checks.', flush=True)
        return
    try:
        result = subprocess.run(['udevadm', 'settle', '--timeout=15'],
                                check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessingError(f'Cannot settle pending device events {stage}: {exc}. '
                              'Processing has stopped; review disk identity and Linux storage services.') from exc
    if result.returncode != 0:
        raise ProcessingError(f'udevadm settle failed {stage} (exit {result.returncode}). '
                              'Processing has stopped; review disk identity and Linux storage services.')


def _same(original: dict, current: dict) -> bool:
    return all(original.get(field) == current.get(field)
               for field in ('path', 'size', 'model', 'serial', 'wwn', 'identity', 'slot', 'hctl', 'diskseq'))


def preflight(original: dict, *, require_no_partitions: bool = False) -> dict:
    """Fresh hardware/system-state check before *every* destructive action."""
    result = inventory.discover()
    cfg = store.read_config()
    candidates = [d for d in result['disks'] if d['path'] == original.get('path')]
    if len(candidates) != 1:
        raise ProcessingError('Selected physical device no longer exists; rescan.')
    disk = candidates[0]
    if not _same(original, disk):
        raise ProcessingError('Drive identity, connection, or capacity changed. Return to drive menu and rescan.')
    if original.get('manual_record_id') and original.get('operator_bound_session'):
        # Reconnection is never inferred: only explicit session binding is carried
        # across fresh hardware checks, and only for a matching size/model.
        try:
            store.confirm_manual_attachment(disk, original['manual_record_id'])
        except ValueError as exc:
            raise ProcessingError(f'Manual drive record mismatch: {exc}') from exc
        disk['manual_record_id'] = original['manual_record_id']
        disk['operator_bound_session'] = True
    if disk.get('size', 0) < MIN_DISK_SIZE:
        raise ProcessingError('Refusing to process an unusually small or zero-size disk.')
    if disk.get('readonly'):
        raise ProcessingError('Drive is read-only.')
    if disk.get('system_reasons'):
        raise ProcessingError('Protected SYSTEM disk: ' + '; '.join(disk['system_reasons']))
    if disk.get('identity') in cfg.get('ignored_drives', []) and disk.get('identity'):
        raise ProcessingError('Drive excluded in configuration')
    if disk.get('slot') in cfg.get('ignored_slots', []) and disk.get('slot'):
        raise ProcessingError('Connection slot excluded in configuration')
    if disk.get('controller_id') in cfg.get('ignored_controllers', []) and disk.get('controller_id'):
        raise ProcessingError('Controller excluded in configuration')
    if disk['path'] not in [f'/dev/{disk["kname"]}'] or not re.fullmatch(r'(sd[a-z]+|hd[a-z]+)', disk['kname']):
        raise ProcessingError('v0.4 destructive operations support directly named /dev/sdX and /dev/hdX disks only.')
    if disk.get('tran', '').lower() == 'nvme' or disk.get('rotational') is False:
        raise ProcessingError('SSD/NVMe detected. Generic overwrite is not an adequate SSD sanitization method; unsupported in v0.4.')
    unsafe = _disk_tree_has_unsafe_nodes(disk) + _swap_and_holders(disk)
    all_majmins = {str(n.get('maj:min')) for n in inventory.flatten(disk['tree']) if n.get('maj:min')}
    try:
        if any(m['majmin'] in all_majmins for m in mounts.live_mounts()):
            unsafe.append('kernel mount table shows this disk/partition mounted')
    except Exception as exc:
        unsafe.append(f'cannot inspect live mount table: {exc}')
    # Add specific mount/holder names to the existing conservative guards.
    # This read-only snapshot is NOT a disk lock and cannot replace identity
    # revalidation immediately before a destructive command.
    try:
        claim_report = storage_claims.inspect_claims(disk)
    except Exception as exc:
        # Unexpected inspection errors must not authorize access.
        unsafe.append(f'storage-claim inspection failed: {exc}')
        claim_report = None
    if unsafe or (claim_report is not None and claim_report['blocked']):
        reason = '; '.join(sorted(set(unsafe)))
        details = storage_claims.describe_claims(claim_report) if claim_report and claim_report['blocked'] else ''
        raise ProcessingError('Device is in use or cannot safely be assessed: '
                              + (reason or 'storage claim detected or inspection incomplete')
                              + ('\n' + details if details else ''))
    if require_no_partitions and disk.get('partitions'):
        raise ProcessingError('Unexpected partitions remain; do not write a report to an ambiguous layout.')
    if any('Suspicious SCSI enumeration:' in message for message in result.get('warnings', [])) and disk.get('hctl'):
        raise ProcessingError('Suspicious duplicate SCSI enumeration present. Resolve ID conflict before writing.')
    # Check that device still has exactly the kernel major/minor we selected.
    expected = original.get('majmin')
    actual = _majmin(disk['path'])
    if expected and actual != expected:
        raise ProcessingError(f'Device number changed ({expected} -> {actual}); rescan')
    return disk


def confirm_action(disk: dict, action: str) -> bool:
    """Two confirmations. For anonymous disks, confirmation is deliberately stricter."""
    print('\n' + '!' * 66)
    print(f'{action} TARGET: {disk["path"]} ({inventory.sizestr(disk["size"])})')
    print(f'Model: {disk.get("model") or "UNKNOWN"}  Serial: {disk.get("serial") or "UNKNOWN"}')
    print(f'Connection: {disk.get("hctl") or disk.get("tran") or "UNKNOWN"}')
    print('All existing data and partitions on this physical disk WILL BE DESTROYED.')
    print('Data recovery MUST be finished first. This cannot be undone.')
    print('!' * 66)
    if disk.get('manual_record_id'):
        rec = store.read_state().get('records', {}).get(disk['manual_record_id'], {})
        expected = rec.get('asset_id') or rec.get('manual_serial')
        if not expected:
            return False
        print('CAUTION: identity was manually entered, not verified from hardware.')
        if input('Type the saved physical-label ID to confirm this specific disk: ').strip() != expected:
            return False
    if input(f'Type {action} to authorize this exact disk: ').strip() != action:
        return False
    code = disk['path'] + (' UNVERIFIED' if not disk.get('identity') else '')
    return input(f'Type {code} to confirm hardware target: ').strip() == code


def _capture_diagnostics(disk: dict) -> dict:
    """Capture evidence before overwrite. Failures are recorded, not hidden."""
    snapshot = {'collected_at': utc_now(), 'disk_before': {k: v for k, v in disk.items() if k != 'tree'}}
    try:
        snapshot['filesystem_before'] = diagnostics.filesystem_report(disk, sudo=True)
    except Exception as exc:
        snapshot['filesystem_before'] = f'Unavailable: {exc}'
    try:
        snapshot['health_before'] = diagnostics.health_report(disk['path'], sudo=True)
        if shutil.which('smartctl'):
            rc, raw, stderr = diagnostics.command(['smartctl', '--all', '--json', disk['path']], sudo=True, timeout=75)
            if raw.strip():
                try:
                    snapshot['smartctl_raw_json'] = json.loads(raw)
                except ValueError:
                    snapshot['smartctl_raw_json_error'] = raw[:2000]
            snapshot['smartctl_exit_bitmask'] = rc
            if stderr.strip():
                snapshot['smartctl_stderr'] = stderr[:1000]
    except Exception as exc:
        snapshot['health_before'] = f'Unavailable: {exc}'
    if disk.get('hctl') and shutil.which('sg_logs'):
        try:
            snapshot['scsi_logs_before'] = diagnostics.scsi_log_report(disk['path'], sudo=True)
        except Exception as exc:
            snapshot['scsi_logs_before'] = f'Unavailable: {exc}'
    return snapshot


def wipe(disk: dict, pattern: str, passes: int, verify: bool) -> dict:
    if pattern not in ('zero', 'random') or not isinstance(passes, int) or not 1 <= passes <= MAX_PASSES:
        raise ProcessingError('Choose zero or random and 1 to 10 passes.')
    disk = preflight(disk)
    if not confirm_action(disk, 'WIPE'):
        raise ProcessingError('Cancelled without changes')
    disk = preflight(disk)
    print('Capturing pre-wipe signatures and drive health (where supported)...', flush=True)
    evidence = _capture_diagnostics(disk)
    op = store.start_processing(disk, 'wipe', {'method': pattern, 'passes_requested': passes,
                                               'verification_requested': verify, 'evidence': evidence,
                                               'passes_completed': 0})
    device = disk['path']
    source = '/dev/zero' if pattern == 'zero' else '/dev/urandom'
    verification = 'NOT COMPLETED' if verify else 'NOT REQUESTED'
    stage = 'before overwrite'
    try:
        for index in range(passes):
            stage = f'preflight before overwrite pass {index + 1}'
            preflight(disk)
            print(f'\nPASS {index + 1} OF {passes}. Do not disconnect this drive.')
            stage = f'overwrite pass {index + 1}'
            run_privileged(['dd', f'if={source}', f'of={device}', 'bs=4M',
                            f'count={disk["size"]}', 'iflag=fullblock,count_bytes',
                            'conv=fsync,notrunc', 'status=progress'])
            # The pass finished before any possible post-pass claim appears.
            store.update_processing(op, {'passes_completed': index + 1})
            stage = f'after overwrite pass {index + 1}'
            _settle_storage_events(stage)
            preflight(disk)
        if verify:
            stage = 'before readback verification'
            preflight(disk)
            stage = 'readback verification'
            if pattern == 'zero':
                print('Verifying every readable logical byte is zero (may take substantial time).')
                run_privileged(['cmp', '-n', str(disk['size']), '--', device, '/dev/zero'])
                verification = 'FULL ZERO READBACK MATCHED'
            else:
                print('Random overwrite: readback check only; cannot verify exact random bytes.')
                run_privileged(['dd', f'if={device}', 'of=/dev/null', 'bs=4M',
                                f'count={disk["size"]}', 'iflag=fullblock,count_bytes', 'status=progress'])
                verification = 'READBACK PASSED; RANDOM CONTENT NOT VERIFIED'
            stage = 'after readback verification'
            _settle_storage_events(stage)
            preflight(disk)
        store.finish_processing(op, success=True, fields={'verification': verification,
                                                          'last_stage': stage})
        print(f'Wipe completed. {verification}. You can now format the drive.')
    except BaseException as exc:
        store.finish_processing(op, success=False,
                                fields={'failure': str(exc), 'failure_stage': stage,
                                        'verification': verification})
        print(f'WIPE STOPPED at: {stage}. Completed passes are retained in history. '
              f'Verification: {verification}. No automatic repair was attempted.', flush=True)
        raise
    return op


def format_drive(disk: dict, fs: str) -> dict:
    if fs not in FORMATTERS:
        raise ProcessingError('Choose FAT32 or exFAT')
    disk = preflight(disk)
    if fs == 'fat32' and disk['size'] > MAX_MBR_SIZE:
        raise ProcessingError('FAT32/MBR layout is not supported for this capacity; use exFAT/GPT.')
    if not shutil.which('parted') or not shutil.which(FORMATTERS[fs]):
        raise ProcessingError('Install missing tools: sudo apt install parted dosfstools exfatprogs')
    if not confirm_action(disk, 'FORMAT'):
        raise ProcessingError('Cancelled without changes')
    disk = preflight(disk)
    layout = 'msdos' if fs == 'fat32' else 'gpt'
    op = store.start_processing(disk, 'format', {'filesystem': fs, 'partition_table': layout})
    stage = 'partition-table creation'
    try:
        if fs == 'fat32':
            cmd = ['parted', '-s', '-a', 'optimal', '--', disk['path'], 'mklabel', 'msdos',
                   'mkpart', 'primary', 'fat32', '1MiB', '100%']
        else:
            cmd = ['parted', '-s', '-a', 'optimal', '--', disk['path'], 'mklabel', 'gpt',
                   'mkpart', 'primary', '1MiB', '100%', 'set', '1', 'msftdata', 'on']
        run_privileged(cmd)
        stage = 'partition table reread'
        if shutil.which('partprobe'):
            run_privileged(['partprobe', '--', disk['path']])
        stage = 'after repartitioning'
        _settle_storage_events(stage)
        after = next((d for d in inventory.discover()['disks'] if d['path'] == disk['path']), None)
        if not after or not _same(disk, after):
            raise ProcessingError('Drive identity changed after repartitioning; stop and investigate.')
        parts = [p for p in after['partitions'] if p.get('type') == 'part']
        if len(parts) != 1:
            raise ProcessingError('Expected exactly one new partition; scan and verify before formatting.')
        part = parts[0]['path']
        _majmin(part)
        preflight(after)
        stage = 'filesystem creation'
        if fs == 'fat32':
            run_privileged(['mkfs.fat', '-F', '32', '-n', 'DRIVEBENCH', part])
        else:
            run_privileged(['mkfs.exfat', '-L', 'DRIVEBENCH', part])
        stage = 'after filesystem creation'
        _settle_storage_events(stage)
        preflight(after)
        store.finish_processing(op, success=True, fields={'partition': part,
                                                          'last_stage': stage})
        print('Partition formatted:', part, fs)
    except BaseException as exc:
        store.finish_processing(op, success=False,
                                fields={'failure': str(exc), 'failure_stage': stage})
        print(f'FORMAT STOPPED at: {stage}. The drive may be partially prepared; '
              'inspect it before attempting further changes.', flush=True)
        raise
    return op


def _report_body(disk: dict, *, record: dict | None, operations: list[dict]) -> str:
    """Readable report plus structured JSON, including pre-wipe evidence."""
    last_wipe = next((x for x in reversed(operations) if x.get('operation') == 'wipe' and x.get('outcome') == 'COMPLETED'), None)
    header = [
        'DRIVEBENCH DRIVE REPORT',
        '=======================',
        f'Report generated (UTC): {utc_now()}',
        f'Device at generation: {disk["path"]}',
        f'Model: {disk.get("model") or "Unknown"}',
        f'Hardware serial: {disk.get("serial") or "Unavailable"}',
        f'Hardware serial source: {disk.get("serial_source") or "Unavailable"}',
        f'Operator-supplied serial: {(record or {}).get("manual_serial") or "Not supplied"} (not hardware verified)',
        f'Asset ID: {(record or {}).get("asset_id") or "Not supplied"}',
        f'History association: {"device-verified" if disk.get("identity") else "operator-confirmed this session only" if record else "unverified"}',
        f'Capacity: {inventory.sizestr(disk["size"])} ({disk["size"]} bytes)',
        f'Inventory record: {record["id"] if record else "UNVERIFIED IDENTITY (no reliable persistent match)"}',
    ]
    if last_wipe:
        header += [f'Last DriveBench completed wipe (UTC): {last_wipe["finished_at"]}',
                   f'Wipe pattern: {last_wipe.get("method")}; passes: {last_wipe.get("passes_completed")}',
                   f'Wipe verification: {last_wipe.get("verification")}']
    else:
        header += ['DriveBench wipe status: NO VERIFIED HISTORICAL WIPE ASSOCIATED WITH THIS DRIVE']
    header += ['', 'Important: A successful logical overwrite does not certify removal of remapped or SSD flash cells.',
               'Power-on hours and diagnostics are firmware-reported, not guaranteed lifetime usage.', '',
               'RECORDED HISTORY, DIAGNOSTICS, PARTITIONS, AND METADATA (JSON)',
               '------------------------------------------------------------']
    obj = {'report_date_utc': utc_now(), 'current_disk': {k: v for k, v in disk.items() if k != 'tree'},
           'inventory_record': record, 'processing_events': operations}
    return '\n'.join(header) + '\n' + json.dumps(obj, indent=2, sort_keys=True, default=str) + '\n'


def _root_dir() -> Path:
    # Use DriveBench's already protected root-owned mount base.
    mounts._ensure_dir(mounts.MOUNT_BASE)
    return mounts.MOUNT_BASE


def write_report(disk: dict, recent_unlinked: list[dict] | None = None) -> str:
    """Mount one freshly prepared FAT32/exFAT partition rw, write report, unmount."""
    disk = preflight(disk)
    parts = [p for p in disk['partitions'] if p.get('type') == 'part']
    if len(parts) != 1 or parts[0].get('fstype') not in ('vfat', 'exfat'):
        raise ProcessingError('Exactly one detected FAT32 or exFAT partition is required.')
    part = parts[0]
    part_device = part['path']
    part_number = _majmin(part_device)
    if part.get('mountpoints'):
        raise ProcessingError('Partition is already mounted. Unmount it before writing the report.')
    # Never write to arbitrary preexisting volumes: require a completed DriveBench
    # format for this hardware, or an explicit same-session unlinked format.
    history_state = store.read_state()
    linked = next((r for r in history_state.get('records', {}).values()
                   if disk.get('identity') and r.get('identity') == disk['identity']), None)
    if not linked and disk.get('operator_bound_session') and disk.get('manual_record_id'):
        linked = store.confirm_manual_attachment(disk, disk['manual_record_id'])
    linked_events = linked.get('operations', []) if linked else []
    evidence = linked_events if linked else (recent_unlinked or [])
    formatted = next((e for e in reversed(evidence)
                      if e.get('operation') == 'format' and e.get('outcome') == 'COMPLETED'), None)
    if not formatted or formatted.get('filesystem') != ('fat32' if part.get('fstype') == 'vfat' else 'exfat'):
        raise ProcessingError('No matching completed DriveBench format record for this drive. '
                              'Refusing to modify an unverified or pre-existing partition.')
    if not confirm_action(disk, 'WRITE REPORT'):
        raise ProcessingError('Cancelled without changes')
    disk = preflight(disk)
    parts = [p for p in disk['partitions'] if p.get('type') == 'part']
    if len(parts) != 1 or parts[0]['path'] != part_device or parts[0].get('fstype') != part.get('fstype') or _majmin(part_device) != part_number:
        raise ProcessingError('Partition changed since confirmation')
    report_id = disk.get('identity')
    records = store.read_state()['records']
    rec = next((value for value in records.values() if report_id and value.get('identity') == report_id), None)
    if not rec and disk.get('operator_bound_session') and disk.get('manual_record_id'):
        rec = store.confirm_manual_attachment(disk, disk['manual_record_id'])
    operations = list(rec.get('operations', [])) if rec else (recent_unlinked or [])
    # Do not include a fabricated wipe if no durable record exists.
    print('Collecting current drive health for the report (where supported)...', flush=True)
    try:
        current_health = diagnostics.health_report(disk['path'], sudo=True)
    except Exception as exc:
        current_health = f'Unavailable: {exc}'
    body = _report_body(disk, record=rec, operations=operations)
    body += '\nCURRENT DRIVE HEALTH (DEVICE-REPORTED)\n--------------------------------------\n' + current_health + '\n'
    op = store.start_processing(disk, 'report', {'partition': part_device, 'report_name': 'DRIVEBENCH-REPORT.txt'})
    target = None
    mounted = False
    try:
        base = _root_dir()
        target = base / ('report-' + uuid.uuid4().hex[:12])
        mounts._ensure_dir(target)
        # File ownership on FAT and exFAT is set to the invoking normal user.
        opts = f'rw,nosuid,nodev,noexec,uid={os.getuid()},gid={os.getgid()},umask=077'
        preflight(disk)
        run_privileged(['mount', '-t', str(part['fstype']), '-o', opts, '--', part_device, str(target)])
        mounted = True
        found = next((m for m in mounts.live_mounts() if m['mountpoint'] == str(target)), None)
        if not found or found['majmin'] != part_number:
            raise ProcessingError('Mounted partition identity mismatch; not writing anything.')
        path = target / 'DRIVEBENCH-REPORT.txt'
        # O_EXCL prevents overwriting existing user data. O_NOFOLLOW prevents symlink following.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        print('Created:', path)
        store.update_processing(op, {'bytes_written': len(body.encode('utf-8'))})
    except BaseException as exc:
        store.finish_processing(op, success=False, fields={'failure': str(exc)})
        raise
    finally:
        if mounted:
            try:
                run_privileged(['umount', '--', str(target)])
            except ProcessingError as exc:
                print('WARNING: report mount could not be released:', exc)
                print('Inspect', target, 'and unmount manually before further drive operations.')
                raise
        if target is not None and target.exists():
            run_privileged(['rmdir', '--', str(target)])
    store.finish_processing(op, success=True)
    print('Report written successfully and temporary writable mount removed.')
    return part_device
