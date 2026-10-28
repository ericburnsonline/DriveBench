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
