"""Read-only evidence scanning and controlled third-party recovery helpers.

No source-device writes are issued by this module. Found signatures are
candidates, not proof of complete or recoverable files. No user file data is
saved in DriveBench's history.
"""
from __future__ import annotations

import collections
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

from . import diagnostics, inventory, processing, store

MIB = 1024 * 1024
CHUNK = 4 * MIB
SIGNATURES = {
    'JPEG': b'\xff\xd8\xff',
    'PNG': b'\x89PNG\r\n\x1a\n',
    'PDF': b'%PDF-',
    'ZIP/DOCX/XLSX': b'PK\x03\x04',
    'GIF': b'GIF8',
    'ELF executable': b'\x7fELF',
    'SQLite': b'SQLite format 3\x00',
    'gzip': b'\x1f\x8b\x08',
    'RAR': b'Rar!\x1a\x07',
}
TAIL = max(map(len, SIGNATURES.values())) - 1


class AnalysisError(RuntimeError):
    pass


def _source(disk: dict) -> dict:
    """Revalidate a whole disk and block protected/excluded devices.

    Unlike processing.preflight, read-only analysis tolerates mounted source
    partitions, read-only media and flash devices. The user still cannot scan
    a system disk or excluded controller via this menu.
    """
    inventory_data = inventory.discover()
    found = [d for d in inventory_data['disks'] if d.get('path') == disk.get('path')]
    if len(found) != 1 or not processing._same(disk, found[0]):
        raise AnalysisError('Drive disappeared or changed. Return to Scan and reselect it.')
    current = found[0]
    # Preserve only a previously operator-confirmed, in-session manual binding.
    if disk.get('operator_bound_session') and disk.get('manual_record_id') and not current.get('identity'):
        try:
            store.confirm_manual_attachment(current, disk['manual_record_id'])
        except ValueError as exc:
            raise AnalysisError(f'Manual drive record no longer matches: {exc}') from exc
        current['operator_bound_session'] = True
        current['manual_record_id'] = disk['manual_record_id']
        current['record_id'] = disk['manual_record_id']
    elif disk.get('record_id') and disk.get('identity') and current.get('identity') == disk['identity']:
        current['record_id'] = disk['record_id']
    cfg = store.read_config()
    if current.get('system_reasons'):
        raise AnalysisError('SYSTEM disk is protected from raw analysis')
    if current.get('identity') and current['identity'] in cfg.get('ignored_drives', []):
        raise AnalysisError('Disk excluded in configuration')
    if current.get('slot') and current['slot'] in cfg.get('ignored_slots', []):
        raise AnalysisError('Connection slot excluded in configuration')
    if current.get('controller_id') and current['controller_id'] in cfg.get('ignored_controllers', []):
        raise AnalysisError('Controller excluded in configuration')
    if any('Suspicious SCSI enumeration:' in w for w in inventory_data.get('warnings', [])) and current.get('hctl'):
        raise AnalysisError('Suspicious SCSI enumeration; resolve possible ID conflict first')
    if int(current.get('size') or 0) <= 0:
        raise AnalysisError('Drive has unknown or zero capacity')
    try:
        diagnostics.checked_device(current['path'])
    except (OSError, diagnostics.DiagnosticError) as exc:
        raise AnalysisError(str(exc)) from exc
    return current


def _sudo_ready():
    if not shutil.which('sudo'):
        raise AnalysisError('sudo missing: sudo apt install sudo')
    try:
        rc = subprocess.run(['sudo', '-v'], timeout=45, check=False).returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AnalysisError(f'Unable to authorize read-only access: {exc}') from exc
    if rc:
        raise AnalysisError('sudo authentication failed; scan not started')


def _ensure_dd():
    if not shutil.which('dd'):
        raise AnalysisError('dd missing. Install with: sudo apt install coreutils')


def _dd_read(device: str, start: int, length: int) -> bytes:
    """Read a bounded slice of the raw device with root *read* permissions."""
    argv = ['sudo', '-n', 'dd', 'if=' + device, 'bs=1M',
            f'skip={start}', f'count={length}', 'iflag=skip_bytes,count_bytes', 'status=none']
    try:
        cp = subprocess.run(argv, capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AnalysisError(f'Raw sample read failed at byte {start}: {exc}') from exc
    if cp.returncode or len(cp.stdout) != length:
        raise AnalysisError(f'Sample read incomplete at byte {start}: {cp.stderr.decode(errors="replace")[-250:]}')
    return cp.stdout


def sample_spans(size: int, length: int = CHUNK) -> list[tuple[int, int]]:
    """Spread samples across the surface; don't overlap on tiny drives."""
    if size <= 0:
        return []
    n = min(size, length)
    offsets = {round((size - n) * f) for f in (0, .25, .5, .75, 1)}
    spans = []
    for p in sorted(offsets):
        if spans and p < spans[-1][0] + spans[-1][1]:
            # Already covered, or partly covered, by a prior sample.
            left = spans[-1][0] + spans[-1][1]
            if left >= p + n:
                continue
            spans.append((left, p + n - left))
        else:
            spans.append((p, n))
    return spans


def _structures(prefix: bytes) -> list[str]:
    findings = []
    if len(prefix) >= 512 and prefix[510:512] == b'\x55\xaa':
        table = prefix[446:510]
        entries = [table[i:i + 16] for i in range(0, 64, 16)]
        if any(x[4] != 0 and int.from_bytes(x[12:16], 'little') > 0 for x in entries):
            findings.append('Possible MBR partition entries')
    if len(prefix) >= 520 and prefix[512:520] == b'EFI PART':
        findings.append('GPT header at LBA 1')
    if len(prefix) >= 1082 and prefix[1080:1082] == b'\x53\xef':
        findings.append('Possible ext2/3/4 superblock (whole-device)')
    if len(prefix) >= 1030 and prefix[1024:1026] in (b'H+', b'HX', b'BD'):
        findings.append('Possible Apple HFS/HFS+ signature (whole-device)')
    if len(prefix) >= 90:
        if prefix[3:11] == b'NTFS    ':
            findings.append('NTFS boot-sector marker (whole-device)')
        if prefix[3:11] == b'EXFAT   ':
            findings.append('exFAT boot-sector marker (whole-device)')
        if b'FAT32' in prefix[82:90] or b'FAT16' in prefix[54:62] or b'FAT12' in prefix[54:62]:
            findings.append('FAT boot-sector marker (whole-device)')
    return findings


class EvidenceCounter:
    def __init__(self):
        self.counts = collections.Counter()
        self.zero_bytes = 0
        self.bytes_read = 0
        self.frequency = collections.Counter()
        self._tail = b''
        self.first_offsets: dict[str, list[int]] = {}

    def feed(self, data: bytes, start: int, *, contiguous: bool):
        previous = self._tail if contiguous else b''
        merged = previous + data
        base = start - len(previous)
        for name, sig in SIGNATURES.items():
            # Include signatures spanning a read boundary, but don't recount
            # those entirely within the previous block.
            from_index = max(0, len(previous) - len(sig) + 1)
            at = merged.find(sig, from_index)
            while at != -1:
                self.counts[name] += 1
                off = base + at
                first = self.first_offsets.setdefault(name, [])
                if len(first) < 8:
                    first.append(off)
                at = merged.find(sig, at + 1)
        self._tail = merged[-TAIL:] if TAIL else b''
        self.zero_bytes += data.count(b'\0')
        self.bytes_read += len(data)
        self.frequency.update(data)

    def result(self, size: int, mode: str, coverage: list[list[int]], structures: list[str], complete: bool) -> dict:
        entropy = 0.0
        if self.bytes_read:
            for count in self.frequency.values():
                p = count / self.bytes_read
                entropy -= p * math.log2(p)
        has_evidence = bool(structures or self.counts)
        return {
            'mode': mode, 'disk_size_bytes': size,
            'bytes_examined': self.bytes_read,
            'coverage_ranges': coverage,
            'fully_scanned': complete,
            'zero_fraction_in_examined_bytes': round(self.zero_bytes / self.bytes_read, 6) if self.bytes_read else None,
            'sample_entropy_bits_per_byte': round(entropy, 3),
            'file_signature_candidates': dict(sorted(self.counts.items())),
            'first_signature_offsets_bytes': self.first_offsets,
            'structural_markers': structures,
            'assessment': ('EVIDENCE_DETECTED' if has_evidence else
                           'NO_RECOGNIZABLE_EVIDENCE_DETECTED' if complete else 'INCONCLUSIVE_SAMPLED_ONLY'),
            'limitations': ('Signature candidates can be false positives, fragments, or embedded content; '
                            'not necessarily complete files. No matches cannot prove unused/erased. '
                            'High entropy alone cannot prove encryption or wiping.'),
        }


def _run_full_scan(device: str, size: int, sink: EvidenceCounter, on_progress=None) -> tuple[int, str, bytes]:
    argv = ['sudo', '-n', 'dd', 'if=' + device, 'bs=4M', 'status=none']
    with __import__('tempfile').TemporaryFile(mode='w+b') as errors:
        # Keep the same login session/controlling TTY as `sudo -v` in
        # _sudo_ready(). Creating a new session here can make sudo -n lose
        # access to the already-authenticated sudo timestamp.
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=errors)
        assert p.stdout is not None
        pos = 0
        progress_mark = 0
        prefix = b''
        try:
            while True:
                block = p.stdout.read(CHUNK)
                if not block:
                    break
                if pos == 0:
                    prefix = block[:2048]
                sink.feed(block, pos, contiguous=True)
                pos += len(block)
                if pos // (256 * MIB) > progress_mark:
                    progress_mark = pos // (256 * MIB)
                    if on_progress:
                        on_progress(pos)
                    print(f'  Read {pos / (1024 ** 3):.2f} GiB of {size / (1024 ** 3):.2f} GiB', flush=True)
            rc = p.wait()
        except BaseException:
            # The process is NOT in its own group; killpg() would risk
            # terminating DriveBench and the invoking terminal too.
            # sudo forwards termination to its child on supported systems.
            if p.poll() is None:
                p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            raise
        finally:
            p.stdout.close()
        errors.seek(0)
        err = errors.read(2048).decode('utf-8', 'replace')
        return rc, err, prefix


def scan(disk: dict, *, full: bool) -> dict:
    disk = _source(disk)
    _ensure_dd()
    _sudo_ready()
    record = store.start_analysis(disk, 'full_scan' if full else 'quick_scan',
                                  {'disk_size_bytes': int(disk['size'])})
    count = EvidenceCounter()
    regions: list[list[int]] = []
    size = int(disk['size'])
    try:
        if full:
            print('Read-only full-surface scan started. Ctrl+C interrupts; unfinished scans stay marked incomplete.')
            rc, err, prefix = _run_full_scan(disk['path'], size, count,
                on_progress=lambda pos: store.update_analysis(record, {'bytes_examined_so_far': pos}))
            regions = [[0, count.bytes_read]]
            if rc or count.bytes_read != size:
                raise AnalysisError(f'Full scan incomplete; read {count.bytes_read} of {size} bytes; dd: {err[:300]}')
        else:
            print('Quick scan samples five regions only; no findings is NOT proof of unused media.')
            prefix = b''
            previous_end = -1
            for off, n in sample_spans(size):
                part = _dd_read(disk['path'], off, n)
                if off == 0:
                    prefix = part[:2048]
                count.feed(part, off, contiguous=(off == previous_end))
                previous_end = off + n
                regions.append([off, n])
        # A USB bridge could have re-enumerated while the read was running.
        _source(disk)
        report = count.result(size, 'full' if full else 'quick', regions, _structures(prefix), full)
        store.finish_analysis(record, report=report, success=True)
        return report
    except BaseException as exc:
        store.finish_analysis(record, report={'bytes_examined': count.bytes_read,
                              'error': str(exc)[:500]}, success=False)
        raise


def _run_diagnostic_command(disk: dict, program: str, args: list[str], *, timeout: int = 1800) -> dict:
    disk = _source(disk)
    if not shutil.which(program):
        raise AnalysisError(f'{program} missing. Install: sudo apt install testdisk')
    _sudo_ready()
    op = store.start_analysis(disk, 'partition_search', {'tool': program})
    try:
        # Commands specified by DriveBench do NOT include partition table writes.
        cp = subprocess.run(['sudo', '-n', program] + args, capture_output=True,
                            text=True, errors='replace', check=False, timeout=timeout)
        snippet = cp.stdout[:12000]
        result = {'tool': program, 'exit_code': cp.returncode,
                  'output_excerpt': snippet, 'stderr_excerpt': cp.stderr[:1000]}
        store.finish_analysis(op, report=result, success=cp.returncode == 0)
        return result
    except BaseException as exc:
        store.finish_analysis(op, report={'error': str(exc)[:500]}, success=False)
        raise


def search_partitions(disk: dict, *, deep: bool = False) -> dict:
    if deep:
        return _run_diagnostic_command(disk, 'testdisk',
                                       ['/cmd', disk['path'], 'analyze,search'], timeout=3600)
    return _run_diagnostic_command(disk, 'testdisk', ['/list', disk['path']], timeout=120)


def _destination_directory(disk: dict, dest: str) -> Path:
    """Fail closed unless dest is on a mounted *different* block device.

    The mount source's lsblk ancestor chain must not include the source disk.
    This deliberately excludes tmpfs/overlay/network destinations in v0.4.2.
    """
    base = Path(dest).expanduser().resolve(strict=True)
    if not base.is_dir() or not os.access(base, os.W_OK | os.X_OK):
        raise AnalysisError('Destination must be an existing writable directory')
    if not shutil.which('findmnt') or not shutil.which('lsblk'):
        raise AnalysisError('Destination safety checks need util-linux (findmnt and lsblk)')
    cp = subprocess.run(['findmnt', '-T', str(base), '-n', '-o', 'SOURCE'],
                        capture_output=True, text=True, check=False, timeout=10)
    mount_source = cp.stdout.strip().splitlines()[0] if cp.stdout.strip() else ''
    mount_source = mount_source.split('[', 1)[0]
    if cp.returncode or not mount_source.startswith('/dev/'):
        raise AnalysisError('Cannot verify a local block-device destination; choose another mounted disk')
    origin = diagnostics.checked_device(disk['path'])
    destination_block = diagnostics.checked_device(mount_source)
    ancestry = subprocess.run(['lsblk', '-rn', '-s', '-o', 'MAJ:MIN', destination_block],
                              capture_output=True, text=True, check=False, timeout=10)
    if ancestry.returncode or not ancestry.stdout.strip():
        raise AnalysisError('Unable to verify destination device ancestry')
    source_dev = os.stat(origin).st_rdev
    source_id = f'{os.major(source_dev)}:{os.minor(source_dev)}'
    if source_id in set(ancestry.stdout.split()):
        raise AnalysisError('Destination resides on the source disk! Choose a different physical disk.')
    return base


def _run_recovery(disk: dict, dest: str, *, image: bool) -> dict:
    disk = _source(disk)
    tool = 'ddrescue' if image else 'photorec'
    package = 'gddrescue' if image else 'testdisk'
    if not shutil.which(tool):
        raise AnalysisError(f'{tool} missing. Install with: sudo apt install {package}')
    destination = _destination_directory(disk, dest)
    _sudo_ready()
    stem = f'{disk.get("record_id") or "unknown-drive"}-{time.strftime("%Y%m%d-%H%M%S")}'
    base = destination / stem
    if image:
        path = base.with_suffix('.img')
        mapfile = base.with_suffix('.map')
        if path.exists() or mapfile.exists():
            raise AnalysisError('Imaging output already exists; choose a fresh destination')
        argv = ['sudo', '-n', 'ddrescue', '-n', str(disk['path']), str(path), str(mapfile)]
    else:
        # CGSecurity adds .1 or later to this base directory automatically.
        if base.exists():
            raise AnalysisError('Recovery output directory already exists')
        argv = ['sudo', '-n', 'photorec', '/d', str(base), '/cmd', str(disk['path']),
                'partition_none,wholespace,search']
    print(f'Writing recovery output ONLY to: {base}')
    if input('Type RECOVER to run the external tool: ').strip() != 'RECOVER':
        raise AnalysisError('Cancelled before starting recovery')
    # Repeat both checks immediately before invoking the external program.
    disk = _source(disk)
    _destination_directory(disk, dest)
    event = store.start_analysis(disk, 'disk_image' if image else 'file_recovery',
                                 {'tool': tool, 'destination': str(base)})
    try:
        # Inherit terminal so user can monitor progress and interact with Ctrl+C.
        rc = subprocess.run(argv, check=False).returncode
        result = {'tool': tool, 'exit_code': rc, 'destination': str(base),
                  'mapfile': str(mapfile) if image else None}
        store.finish_analysis(event, report=result, success=rc == 0)
        return result
    except BaseException as exc:
        store.finish_analysis(event, report={'error': str(exc)[:500], 'destination': str(base)}, success=False)
        raise


def create_image(disk: dict, dest: str) -> dict:
    return _run_recovery(disk, dest, image=True)


def recover_files(disk: dict, dest: str) -> dict:
    return _run_recovery(disk, dest, image=False)


def history_for(disk: dict) -> list[dict]:
    state = store.read_state()
    rid = disk.get('record_id')
    if rid:
        return list(state.get('records', {}).get(rid, {}).get('raw_analysis', []))
    return []  # Anonymous drives need confirmed reattachment; do not guess by /dev/sdX.


def format_summary(result: dict) -> str:
    out = [f'Assessment: {result.get("assessment", "INCOMPLETE / CHECK DETAILS")}',
           f'Examined bytes: {result.get("bytes_examined", 0):,}',
           f'Entire surface read: {"yes" if result.get("fully_scanned") else "no"}',
           f'Zero-filled fraction in examined data: {result.get("zero_fraction_in_examined_bytes")}',
           f'Entropy (sample estimate): {result.get("sample_entropy_bits_per_byte")}',
           'Possible filesystem/partition markers: ' + (', '.join(result.get('structural_markers', [])) or 'none')]
    for kind, amount in result.get('file_signature_candidates', {}).items():
        out.append(f'  {kind}: {amount} candidate header(s)')
    out.append(result.get('limitations', 'No findings do not prove a drive has never been used.'))
    return '\n'.join(out)
