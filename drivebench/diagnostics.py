
"""Read-only filesystem signature and drive-health reports."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


class DiagnosticError(RuntimeError):
    pass


def checked_device(path: str) -> str:
    """Constrain probes to actual kernel block devices, not arbitrary files."""
    resolved = Path(path).resolve()
    if not str(resolved).startswith('/dev/') or not stat.S_ISBLK(resolved.stat().st_mode):
        raise DiagnosticError(f'Not a Linux block device: {path}')
    return str(resolved)


def command(args: list[str], *, sudo: bool = False, timeout: int = 35) -> tuple[int, str, str]:
    argv = (['sudo'] if sudo else []) + args
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, errors='replace',
                            timeout=timeout, check=False)
        return cp.returncode, cp.stdout, cp.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DiagnosticError(f'Unable to run {args[0]}: {exc}') from exc


def _export_values(text: str) -> dict:
    fields = {}
    for line in text.splitlines():
        if '=' in line:
            key, val = line.split('=', 1)
            fields[key] = val
    return fields


def interpret_pttype(pttype: str | None, partitions: int, direct_fstype: str | None = None) -> str:
    if pttype:
        return f'Partition table recognized: {pttype}.'
    if partitions:
        return 'PTTYPE unknown, but Linux lists child partitions. Inspect signatures; do not assume blank.'
    if direct_fstype:
        return f'No recognized partition table; whole-device filesystem detected: {direct_fstype}.'
    return ('No partition table identified. This does NOT establish that the disk is blank. '
            'It may be unpartitioned, damaged, or use an unsupported legacy layout.')


def filesystem_report(disk: dict, *, sudo: bool = False) -> str:
    lines = [f'FILESYSTEM / PARTITION INSPECTION: {disk["path"]}',
             'READ ONLY. Signature detection is not proof a filesystem is healthy.']
    nodes = [disk['path']] + [p['path'] for p in disk.get('partitions', [])
                              if p.get('type') == 'part' and p.get('path')]
    for device in nodes:
        target = checked_device(device)
        lines.append(f'\n{target}')
        if shutil.which('blkid'):
            rc, output, err = command(['blkid', '-p', '-o', 'export', '-c', '/dev/null', target], sudo=sudo)
            fields = _export_values(output)
            if fields:
                for key in ('TYPE', 'PTTYPE', 'USAGE', 'LABEL', 'UUID', 'VERSION'):
                    if fields.get(key):
                        lines.append(f'  {key}: {fields[key]}')
            else:
                if rc == 2 and not err.strip():
                    lines.append('  blkid: no recognized signature (or no readable signature)')
                else:
                    lines.append(f'  blkid: inconclusive (code {rc}): {err.strip()[:160]}')
            if rc == 8:
                lines.append('  WARNING: ambiguous signatures; do not modify or mount blindly.')
        else:
            lines.append('  blkid missing: sudo apt install util-linux')
        if shutil.which('wipefs'):
            rc, output, err = command(['wipefs', '--no-act', target], sudo=sudo)
            if rc == 0 and output.strip():
                lines.append('  Known signatures (wipefs --no-act; nothing erased):')
                lines.extend('    ' + s for s in output.strip().splitlines()[:18])
            elif rc == 0:
                lines.append('  wipefs: no known signatures')
            else:
                lines.append(f'  wipefs: inconclusive (code {rc}): {err.strip()[:160]}')
        else:
            lines.append('  wipefs missing: sudo apt install util-linux')
    lines.append('\n' + interpret_pttype(disk.get('pttype'), len(disk.get('partitions', [])),
                                           disk.get('fstype')))
    lines.append('Unknown/ambiguous findings are NOT evidence that data can safely be erased.')
    return '\n'.join(lines)


def _nested(data: dict, *path):
    value = data
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def summarize_smart(data: dict) -> str:
    lines = ['SMART / SCSI DIAGNOSTIC SUMMARY (device-reported, not a surface test)']
    identity = data.get('model_name') or data.get('scsi_model_name') or data.get('model_family') or '?'
    lines.append(f'Model: {identity}')
    for label, value in [
        ('Serial', data.get('serial_number')),
        ('Protocol', data.get('device', {}).get('protocol') if isinstance(data.get('device'), dict) else None),
        ('Power-on hours', _nested(data, 'power_on_time', 'hours')),
        ('Temperature (C)', _nested(data, 'temperature', 'current')),
        ('SMART health passed', _nested(data, 'smart_status', 'passed')),
        ('SCSI grown defects', data.get('scsi_grown_defect_list')),
        ('SCSI uncorrected read errors', _nested(data, 'scsi_error_counter_log', 'read', 'total_uncorrected_errors')),
        ('SCSI uncorrected write errors', _nested(data, 'scsi_error_counter_log', 'write', 'total_uncorrected_errors')),
        ('NVMe media errors', _nested(data, 'nvme_smart_health_information_log', 'media_errors')),
        ('NVMe percentage used', _nested(data, 'nvme_smart_health_information_log', 'percentage_used')),
    ]:
        if value is not None:
            lines.append(f'{label}: {value}')
    for a in _nested(data, 'ata_smart_attributes', 'table') or []:
        if not isinstance(a, dict):
            continue
        name = str(a.get('name', ''))
        if name in ('Reallocated_Sector_Ct', 'Current_Pending_Sector', 'Offline_Uncorrectable',
                    'Power_On_Hours', 'UDMA_CRC_Error_Count'):
            raw = _nested(a, 'raw', 'value')
            if raw is not None:
                lines.append(f'ATA {name}: {raw}')
    if len(lines) <= 2:
        lines.append('No familiar health counters reported; older SCSI drives may expose other log pages.')
    lines.append('Missing fields mean unavailable, NOT zero or passed.')
    return '\n'.join(lines)


def health_report(path: str, *, sudo: bool = False, usb_sat: bool = False) -> str:
    dev = checked_device(path)
    if not shutil.which('smartctl'):
        return 'smartctl missing. Install with: sudo apt install smartmontools'
    argv = ['smartctl', '--all', '--json'] + (['-d', 'sat'] if usb_sat else []) + [dev]
    rc, output, err = command(argv, sudo=sudo, timeout=75)
    try:
        data = json.loads(output)
        if not isinstance(data, dict):
            raise ValueError('expected object')
    except ValueError:
        return f'smartctl did not return JSON (exit {rc}). {err.strip()[:350]}\n' + output[:1000]
    summary = summarize_smart(data)
    if rc:
        summary += f'\nsmartctl exit status bitmask: {rc}. Nonzero can signal health findings or command errors.'
    messages = _nested(data, 'smartctl', 'messages') or []
    for item in messages[:8]:
        if isinstance(item, dict) and item.get('string'):
            summary += '\nsmartctl: ' + str(item['string'])
    if not sudo and ('Permission denied' in (err + summary) or rc & 2):
        summary += '\nSome information may require elevated privileges. Try again with sudo.'
    return summary


def scsi_log_report(path: str, *, sudo: bool = False) -> str:
    """Show additional vendor-supported SCSI log pages, without issuing writes."""
    dev = checked_device(path)
    if not shutil.which('sg_logs'):
        return 'sg_logs missing. Install with: sudo apt install sg3-utils'
    rc, out, err = command(['sg_logs', '--all', dev], sudo=sudo, timeout=60)
    body = out[:16000].strip() or '(no log pages returned)'
    note = '\n[Output truncated; sg_logs may contain vendor-specific counters.]' if len(out) > 16000 else ''
    return (f'SCSI LOG SENSE data for {dev} (read-only); exit code {rc}\n'
            f'{body}{note}\n{err.strip()[:400]}\n'
            'Not all SCSI log fields correspond to standardized SMART attributes.')


# Structured, seller-safe metrics. These are device-reported readings,
# not replacements for a surface scan or verified overwrite.
SMART_METRIC_LABELS = {
    'ata_reallocated_sectors': 'ATA reallocated sectors',
    'ata_reallocation_events': 'ATA reallocation events',
    'ata_pending_sectors': 'ATA current pending sectors',
    'ata_offline_uncorrectable': 'ATA offline uncorrectable sectors',
    'ata_reported_uncorrectable': 'ATA reported uncorrectable errors',
    'ata_command_timeouts': 'ATA command timeouts',
    'ata_udma_crc_errors': 'ATA UDMA CRC errors (SATA link)',
    'ata_power_cycles': 'ATA power cycle count',
    'ata_load_cycles': 'ATA load cycle count',
    'scsi_grown_defects': 'SCSI grown defect count',
    'scsi_read_uncorrected': 'SCSI uncorrected read errors',
    'scsi_write_uncorrected': 'SCSI uncorrected write errors',
    'scsi_verify_uncorrected': 'SCSI uncorrected verify errors',
    'nvme_media_errors': 'NVMe media/data integrity errors',
    'nvme_error_log_entries': 'NVMe error information log entries',
    'nvme_unsafe_shutdowns': 'NVMe unsafe shutdowns',
    'nvme_percentage_used': 'NVMe percentage used (device estimate)',
    'power_on_hours': 'Power-on hours (device-reported)',
    'temperature_c': 'Temperature (C at collection)',
}

_ATA_COUNTER_IDS = {
    5: 'ata_reallocated_sectors',
    12: 'ata_power_cycles',
    187: 'ata_reported_uncorrectable',
    188: 'ata_command_timeouts',
    193: 'ata_load_cycles',
    196: 'ata_reallocation_events',
    197: 'ata_pending_sectors',
    198: 'ata_offline_uncorrectable',
    199: 'ata_udma_crc_errors',
}


def _whole_number(value):
    """Unknown is never silently converted to zero."""
    if type(value) is int:
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value.strip())
    return None


def _metric_values(data: dict) -> tuple[dict, list[dict]]:
    metrics, attrs = {}, []

    def add(key, value):
        number = _whole_number(value)
        if number is not None:
            metrics[key] = number

    add('power_on_hours', _nested(data, 'power_on_time', 'hours'))
    add('temperature_c', _nested(data, 'temperature', 'current'))
    for row in _nested(data, 'ata_smart_attributes', 'table') or []:
        if not isinstance(row, dict):
            continue
        ident = _whole_number(row.get('id'))
        if ident is None or ident > 255:
            continue
        raw = _whole_number(_nested(row, 'raw', 'value'))
        attrs.append({'id': ident, 'name': str(row.get('name') or '')[:70],
                      'raw': raw, 'normalized': _whole_number(row.get('value')),
                      'worst': _whole_number(row.get('worst')),
                      'threshold': _whole_number(row.get('thresh'))})
        key = _ATA_COUNTER_IDS.get(ident)
        if key and raw is not None:
            metrics[key] = raw
        if ident == 9 and 'power_on_hours' not in metrics:
            add('power_on_hours', raw)

    add('scsi_grown_defects', data.get('scsi_grown_defect_list'))
    for category in ('read', 'write', 'verify'):
        add(f'scsi_{category}_uncorrected', _nested(
            data, 'scsi_error_counter_log', category, 'total_uncorrected_errors'))

    nvme = data.get('nvme_smart_health_information_log')
    if isinstance(nvme, dict):
        add('nvme_media_errors', nvme.get('media_errors'))
        add('nvme_error_log_entries', nvme.get('num_err_log_entries'))
        add('nvme_unsafe_shutdowns', nvme.get('unsafe_shutdowns'))
        add('nvme_percentage_used', nvme.get('percentage_used'))
        if 'power_on_hours' not in metrics:
            add('power_on_hours', nvme.get('power_on_hours'))
        if 'temperature_c' not in metrics:
            add('temperature_c', nvme.get('temperature'))
    return metrics, attrs


def smart_snapshot_from_json(data: dict, disk: dict, *, exit_status: int = 0,
                             probe: str = 'auto', captured_at: str | None = None) -> dict:
    """Normalize smartctl JSON into a persistable read-only health snapshot."""
    from datetime import datetime, timezone
    if not isinstance(data, dict):
        raise DiagnosticError('SMART JSON must be an object')
    metrics, attributes = _metric_values(data)
    health = _nested(data, 'smart_status', 'passed')
    health = health if type(health) is bool else None
    device = data.get('device')
    protocol = device.get('protocol') if isinstance(device, dict) else None
    serial = data.get('serial_number')
    model = data.get('model_name') or data.get('scsi_model_name') or disk.get('model')
    errors = []
    for message in (_nested(data, 'smartctl', 'messages') or [])[:6]:
        if isinstance(message, dict) and message.get('string'):
            errors.append(str(message['string'])[:200])
    if exit_status & 3:
        errors.append(f'smartctl command/device error bits set (exit status {exit_status})')
    if not metrics and health is None:
        status = 'UNAVAILABLE'
    elif exit_status & 3:
        status = 'PARTIAL'
    else:
        status = 'COLLECTED'
    return {
        'schema_version': 1,
        'collected_at': captured_at or datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'device_at_collection': str(disk.get('path') or ''),
        'drive_identity': disk.get('identity'),
        'capacity_bytes': disk.get('size'),
        'model': str(model or ''),
        'serial': str(serial or ''),
        'protocol': str(protocol or ''),
        'probe': probe,
        'smartctl_exit_status': exit_status,
        'collection_status': status,
        'smart_passed': health,
        'metrics': metrics,
        'ata_attributes': attributes,
        'messages': errors,
        'interpretation': ('Device-reported SMART; no guarantee of media health, '
                           'data recoverability, or secure sanitization.'),
    }


def collect_smart_snapshot(disk: dict, *, sudo: bool = False,
                           usb_sat: bool = False) -> dict:
    """Gather read-only SMART data; fall back to SAT on compatible USB bridges."""
    if not shutil.which('smartctl'):
        raise DiagnosticError('smartctl missing. Install: sudo apt install smartmontools')
    path = checked_device(disk['path'])

    def probe(mode):
        argv = ['smartctl', '--all', '--json']
        if mode == 'sat':
            argv += ['-d', 'sat']
        argv += [path]
        rc, output, err = command(argv, sudo=sudo, timeout=75)
        try:
            parsed = json.loads(output)
        except ValueError as exc:
            raise DiagnosticError(f'smartctl returned invalid JSON using {mode}: '
                                  f'{err.strip()[:180]}') from exc
        snap = smart_snapshot_from_json(parsed, disk, exit_status=rc, probe=mode)
        if err.strip():
            snap['messages'].append(err.strip()[:200])
        return snap

    if usb_sat:
        return probe('sat')
    auto = probe('auto')
    if (str(disk.get('tran') or '').lower() != 'usb' or
            auto['smart_passed'] is False or
            any(key.startswith('ata_') for key in auto['metrics']) or
            any(key.startswith('nvme_') for key in auto['metrics']) or
            any(key.startswith('scsi_') for key in auto['metrics'])):
        return auto
    try:
        sat = probe('sat')
    except DiagnosticError:
        return auto
    if (len(sat['metrics']) > len(auto['metrics']) or
            (auto['smart_passed'] is None and sat['smart_passed'] is not None)):
        return sat
    return auto


def compare_smart_snapshots(before: dict, after: dict) -> dict:
    """Compare only matching drives; unavailable and reset counters remain explicit."""
    b_key, a_key = before.get('drive_identity'), after.get('drive_identity')
    b_ser, a_ser = before.get('serial'), after.get('serial')
    b_model, a_model = before.get('model'), after.get('model')
    size_b, size_a = before.get('capacity_bytes'), after.get('capacity_bytes')
    if size_b is not None and size_a is not None and size_b != size_a:
        raise DiagnosticError('SMART comparison refused: drive capacity changed')
    if b_key and a_key and b_key != a_key:
        raise DiagnosticError('SMART comparison refused: hardware identity changed')
    if b_ser and a_ser and b_ser != a_ser:
        raise DiagnosticError('SMART comparison refused: device-reported serial changed')
    if b_model and a_model and b_model != a_model:
        raise DiagnosticError('SMART comparison refused: model changed')
    if b_key and a_key and b_key == a_key:
        confidence = 'INVENTORY_HARDWARE_IDENTITY'
    elif b_ser and a_ser and b_ser == a_ser:
        confidence = 'MATCHING_DEVICE_REPORTED_SERIAL'
    else:
        raise DiagnosticError('SMART comparison requires matching hardware identities '
                              'or matching device-reported serials')
    if (before.get('collection_status') == 'UNAVAILABLE' or
            after.get('collection_status') == 'UNAVAILABLE'):
        raise DiagnosticError('Cannot compare an unavailable SMART collection')
    b_metrics = before.get('metrics') or {}
    a_metrics = after.get('metrics') or {}
    rows = []
    for key, label in SMART_METRIC_LABELS.items():
        b = _whole_number(b_metrics.get(key))
        a = _whole_number(a_metrics.get(key))
        if b is None and a is None:
            continue
        delta = a - b if b is not None and a is not None else None
        rows.append({'metric': key, 'label': label, 'before': b,
                     'after': a, 'delta': delta,
                     'change': ('NOT_COMPARABLE' if delta is None else
                                'INCREASED' if delta > 0 else
                                'DECREASED' if delta < 0 else 'UNCHANGED')})
    return {
        'before_at': before.get('collected_at'),
        'after_at': after.get('collected_at'),
        'identity_confidence': confidence,
        'smart_before': before.get('smart_passed'),
        'smart_after': after.get('smart_passed'),
        'rows': rows,
        'limitations': ('Missing counters are unavailable, not zero. Device-specific '
                        'raw counters may reset, wrap, or have vendor-specific units. '
                        'A SMART pass does not establish a healthy disk.'),
    }
