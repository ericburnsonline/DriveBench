
#!/usr/bin/env python3
"""Standalone USB archive: image, copy, recover, and optionally wipe USB disks.

Ubuntu: sudo apt install gddrescue testdisk util-linux
Configuration: /etc/drivebench/usb_archive.ini (not inside your Git repository).

Archive-only mode (interactive):
    sudo python3 usb_archive_recover.py

Unattended mode (after ONE explicit drive selection/erase authorization):
    sudo python3 usb_archive_recover.py --unattended

WARNING: --unattended ALWAYS zeroes the selected USB disk after all archive
checks succeed. It stops and DOES NOT START WIPING if any prior stage fails.
A readback failure during wiping means the disk may be PARTIALLY erased.

Parallel sessions are supported for DIFFERENT USB disks. Source/destination
locks are cooperative with other copies of this script, not with unrelated
third-party disk utilities. Use archive-only mode unless erasure is intended.
"""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import fcntl
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

CONFIG_PATH = Path('/etc/drivebench/usb_archive.ini')
LOCK_DIR = Path('/run/lock/usb_archive_recover')
CHUNK = 4 * 1024 * 1024
FREE_SPACE_FILESYSTEMS = {'vfat', 'fat', 'fat12', 'fat16', 'fat32', 'ntfs',
                          'ext2', 'ext3', 'ext4'}


def run(command, *, capture=False, check=True, cwd=None, stdin=None):
    args = [str(a) for a in command]
    print('+', ' '.join(args), flush=True)
    return subprocess.run(args, text=True, capture_output=capture,
                          check=check, cwd=cwd, stdin=stdin)


def lsblk():
    raw = subprocess.check_output([
        'lsblk', '--json', '--bytes', '-o',
        'PATH,NAME,TYPE,TRAN,RM,RO,SIZE,MODEL,SERIAL,FSTYPE,MOUNTPOINTS'
    ], text=True)
    return json.loads(raw)['blockdevices']


def flatten(nodes):
    for node in nodes:
        yield node
        yield from flatten(node.get('children') or [])


def usb_disks():
    return [n for n in lsblk() if n.get('type') == 'disk' and
            n.get('tran') == 'usb' and n.get('path')]


def human_size(n):
    n = float(n or 0)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if n < 1024 or unit == 'TiB':
            return f'{n:.1f} {unit}'
        n /= 1024


def all_paths(node):
    return {n.get('path') for n in flatten([node]) if n.get('path')}


def mounts(node):
    return [(n.get('path'), m) for n in flatten([node])
            for m in (n.get('mountpoints') or []) if m]


def block_ancestors(path):
    proc = run(['lsblk', '-s', '-n', '-o', 'PATH', path],
               capture=True, check=False)
    if proc.returncode:
        raise RuntimeError(f'Cannot determine underlying disk of {path}')
    return set(proc.stdout.split()) | {path}


def filesystem_at(path):
    proc = run(['findmnt', '--json', '-o', 'SOURCE,TARGET,FSTYPE',
                '--target', str(path)], capture=True)
    entries = json.loads(proc.stdout).get('filesystems', [])
    if len(entries) != 1:
        raise RuntimeError('Cannot determine archive destination filesystem')
    return entries[0]


def destination_safe(output, source, *, original=None):
    """Require an actual, still-mounted, separate non-root destination partition."""
    if not output.is_dir():
        raise RuntimeError(f'Backup destination is not an existing directory: {output}')
    m = filesystem_at(output)
    mounted_source = (m.get('source') or '').split('[', 1)[0]
    if m.get('target') == '/':
        raise RuntimeError('Archive path is on the root filesystem. Mount backup partition first.')
    if not mounted_source.startswith('/dev/'):
        raise RuntimeError(f'Archive path must be a mounted local partition: {m}')
    dest_ancestors = block_ancestors(mounted_source)
    if dest_ancestors & all_paths(source):
        raise RuntimeError('Archive destination is on the USB drive selected for wiping!')
    root = filesystem_at(Path('/'))
    root_source = (root.get('source') or '').split('[', 1)[0]
    if root_source.startswith('/dev/') and block_ancestors(root_source) & all_paths(source):
        raise RuntimeError('Refusing to process the disk containing the operating system')
    # Prevent a backup partition being unplugged/unmounted midway, which
    # would otherwise cause files to be silently written to the root disk.
    stable = (m.get('source'), m.get('target'), m.get('fstype'))
    if original is not None and stable != original:
        raise RuntimeError('Destination partition changed or was unmounted during processing')
    return stable


def _lock_directory():
    """Keep cooperative locks in a root-owned, non-world-writable directory."""
    LOCK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = LOCK_DIR.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or
            info.st_mode & 0o022):
        raise RuntimeError(f'Unsafe USB archive lock directory: {LOCK_DIR}')
    return LOCK_DIR


@contextmanager
def _file_lock(name, *, shared=False, description='resource'):
    """Cross-process advisory lock; FD stays open until the operation finishes.

    Lock files are intentionally persistent. Deleting them would permit two
    processes to hold different locks for the same resource simultaneously.
    """
    path = _lock_directory() / name
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_CLOEXEC', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError(f'Unsafe USB archive lock file: {path}')
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) |
                        fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f'{description} is already in use by another archive process. '
                               'Choose another USB drive or wait for that job to finish.') from exc
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _device_lock_name(devnum):
    """Current kernel major:minor; aliases for one disk share the same lock."""
    return f'disk-{os.major(devnum)}-{os.minor(devnum)}.lock'


@contextmanager
def reserve_source(original):
    """Prevent two copies of this utility from processing the same disk."""
    with _file_lock(_device_lock_name(original['devnum']),
                    description=f'Source drive {original["path"]}'):
        # A disk might have been disconnected while awaiting a lock.
        verify_source(original)
        print(f'Exclusive archive lock held for {original["path"]}.')
        yield


def _destination_disks(output):
    """Find underlying physical disks of an actual mounted archive filesystem."""
    mounted = filesystem_at(output)
    source = (mounted.get('source') or '').split('[', 1)[0]
    if mounted.get('target') == '/' or not source.startswith('/dev/'):
        raise RuntimeError('Archive destination must be a separate mounted block filesystem')
    proc = run(['lsblk', '-s', '-n', '-o', 'PATH,TYPE', '--', source],
               capture=True, check=False)
    if proc.returncode:
        raise RuntimeError('Cannot identify physical disks below archive destination')
    keys = set()
    for line in proc.stdout.splitlines():
        fields = line.strip().split()
        if len(fields) != 2 or fields[1] != 'disk':
            continue
        st = os.stat(fields[0])
        if not stat.S_ISBLK(st.st_mode):
            raise RuntimeError(f'Destination ancestor is not a block disk: {fields[0]}')
        keys.add(_device_lock_name(st.st_rdev))
    if not keys:
        raise RuntimeError('No physical disk found under archive destination')
    return sorted(keys)


@contextmanager
def reserve_destination(output):
    """Share destination disks with other archivers, but block source selection.

    The locks are held for the entire copy/recovery/wipe sequence. Two jobs may
    write to distinct archive folders on the same large destination volume.
    """
    with ExitStack() as stack:
        for name in _destination_disks(output):
            stack.enter_context(_file_lock(name, shared=True,
                                           description='Archive destination disk'))
        yield


@contextmanager
def reserve_loop_allocation():
    """Serialize losetup --find across copies of this tool."""
    with _file_lock('loop-allocator.lock', description='Loop-device allocation'):
        yield


def disk_identity(node):
    """Capture kernel disk generation in addition to model/size/serial/path."""
    path = node['path']
    st = os.stat(path)
    if not stat.S_ISBLK(st.st_mode):
        raise RuntimeError(f'Not a block device: {path}')
    name = Path(path).name
    sysfs = Path('/sys/class/block') / name
    diskseq_path = sysfs / 'diskseq'
    return {
        'path': path,
        'size': int(node.get('size') or 0),
        'serial': str(node.get('serial') or '').strip(),
        'model': str(node.get('model') or '').strip(),
        'devnum': st.st_rdev,
        'sysfs_device': os.path.realpath(sysfs / 'device'),
        'diskseq': diskseq_path.read_text().strip() if diskseq_path.is_file() else None,
    }


def verify_source(original):
    path = original['path']
    current = next((n for n in usb_disks() if n['path'] == path), None)
    if current is None:
        raise RuntimeError(f'{path} is no longer a USB-attached disk')
    now = disk_identity(current)
    for k in ('path', 'size', 'serial', 'model', 'devnum', 'sysfs_device', 'diskseq'):
        if now[k] != original[k]:
            raise RuntimeError(f'Disk identity changed ({k}); refusing to continue')
    if current.get('ro'):
        raise RuntimeError('Source disk is read-only')
    if original['size'] <= 0:
        raise RuntimeError('Invalid disk size')
    holders = Path('/sys/class/block') / Path(path).name / 'holders'
    if holders.is_dir() and any(holders.iterdir()):
        raise RuntimeError('Source disk has device-mapper or other holders; refuse to continue')
    return current


def unmount_usb(original):
    """Un-mount ordinary automounts. Never force-unmount busy partitions."""
    current = verify_source(original)
    active = mounts(current)
    if active:
        print('Unmounting source USB partitions before imaging:', active)
    for _, mountpoint in reversed(active):
        if not mountpoint.startswith('/'):
            raise RuntimeError(f'Unexpected source mountpoint {mountpoint}; not unmounting')
        run(['umount', '--', mountpoint])
        current = verify_source(original)
    if mounts(current):
        raise RuntimeError(f'Source still mounted: {mounts(current)}')


def digest_file(path):
    h = hashlib.sha256()
    with open(path, 'rb', buffering=0) as fp:
        for chunk in iter(lambda: fp.read(CHUNK), b''):
            h.update(chunk)
    return h.hexdigest()


def image_disk(source, image_path, map_path):
    result = run(['ddrescue', '--no-scrape', source, image_path, map_path], check=False)
    if result.returncode:
        raise RuntimeError('ddrescue encountered an error; no wipe permitted')
    # Ensure the map has no missed/failed blocks even if the executable
    # unexpectedly reports success. The independent SHA-256 read also verifies.
    ranges = []
    for line in map_path.read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        tokens = line.split()
        if len(tokens) != 3:
            continue
        try:
            start = int(tokens[0], 0)
            size = int(tokens[1], 0)
        except ValueError:
            continue
        if tokens[2] in {'+', '-', '?', '*', '/', '%'}:
            ranges.append((start, size, tokens[2]))
    if not ranges or any(status != '+' for _, _, status in ranges):
        raise RuntimeError('ddrescue map shows unfinished/unreadable sectors')
    cursor = 0
    for start, count, _ in ranges:
        if start != cursor:
            raise RuntimeError('ddrescue map has an unaccounted image region')
        cursor += count
    return cursor


def make_loop(image_path):
    with reserve_loop_allocation():
        proc = run(['losetup', '--find', '--show', '--partscan', '--read-only', image_path],
                   capture=True)
    loop = proc.stdout.strip()
    if not re.fullmatch(r'/dev/loop\d+', loop):
        raise RuntimeError(f'Unexpected loop device: {loop!r}')
    run(['udevadm', 'settle'], check=False)
    time.sleep(0.5)
    root = next((n for n in lsblk() if n.get('path') == loop), None)
    if not root:
        run(['losetup', '--detach', loop])
        raise RuntimeError(f'Cannot find loop device {loop}')
    parts = [p for p in flatten(root.get('children') or []) if p.get('type') == 'part']
    return loop, parts or [root]


def files_and_symlinks(root):
    items = {}
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            p = Path(base) / name
            if p.is_symlink():
                dirs.remove(name)
                items[str(p.relative_to(root))] = p
        for name in files:
            p = Path(base) / name
            items[str(p.relative_to(root))] = p
    return items


def copy_existing(source_mount, target):
    target.mkdir(mode=0o700)
    for base, dirs, files in os.walk(source_mount, followlinks=False):
        rel = Path(base).relative_to(source_mount)
        newbase = target / rel
        newbase.mkdir(exist_ok=True)
        for name in list(dirs):
            src = Path(base) / name
            if src.is_symlink():
                dirs.remove(name)
                os.symlink(os.readlink(src), newbase / name)
        for name in files:
            src = Path(base) / name
            dst = newbase / name
            if src.is_symlink():
                os.symlink(os.readlink(src), dst)
            elif src.is_file():
                shutil.copy2(src, dst, follow_symlinks=False)
            else:
                raise RuntimeError(f'Unsupported device/special file: {src}')
    source_items = files_and_symlinks(source_mount)
    target_items = files_and_symlinks(target)
    if source_items.keys() != target_items.keys():
        missing = source_items.keys() - target_items.keys()
        extra = target_items.keys() - source_items.keys()
        raise RuntimeError(f'Copy list mismatch: missing={list(missing)[:5]}, extra={list(extra)[:5]}')
    nfiles = nlinks = nbytes = 0
    for rel, src in source_items.items():
        dst = target_items[rel]
        if src.is_symlink():
            if not dst.is_symlink() or os.readlink(src) != os.readlink(dst):
                raise RuntimeError(f'Symbolic-link mismatch: {rel}')
            nlinks += 1
        elif (not src.is_file() or not dst.is_file() or
              src.stat().st_size != dst.stat().st_size or
              digest_file(src) != digest_file(dst)):
            raise RuntimeError(f'File verification failed: {rel}')
        else:
            nfiles += 1
            nbytes += src.stat().st_size
    return {'files': nfiles, 'bytes': nbytes, 'symlinks': nlinks, 'sha256_verified': True}


def effective_recovery_mode(requested, fstype):
    fs = (fstype or '').lower()
    if requested == 'auto':
        return 'freespace' if fs in FREE_SPACE_FILESYSTEMS else 'wholespace'
    if requested == 'freespace' and fs not in FREE_SPACE_FILESYSTEMS:
        raise RuntimeError(f'PhotoRec freespace scan not supported/verified for {fs or "unknown"}; use auto or wholespace')
    return requested


def inventory_recovered(folder):
    """Hash carved files to document which results exist, not their original integrity."""
    total = 0
    count = 0
    manifest = folder.parent / 'recovered_files.sha256'
    with manifest.open('w', encoding='utf-8') as out:
        for root, _, files in os.walk(folder):
            for name in sorted(files):
                path = Path(root) / name
                if not path.is_file() or path.is_symlink():
                    raise RuntimeError(f'Unexpected special file recovered: {path}')
                rel = path.relative_to(folder)
                h = digest_file(path)
                # JSON escaped filenames can handle unusual characters/newlines.
                out.write(json.dumps({'sha256': h, 'path': str(rel)}, ensure_ascii=True) + '\n')
                count += 1
                total += path.stat().st_size
    return {'files_carved': count, 'bytes': total, 'manifest': str(manifest),
            'note': 'Carving cannot prove completeness or original file integrity'}


def process_partitions(parts, folder, recovery_request):
    results = []
    for index, part in enumerate(parts, 1):
        label = f'partition_{index:02d}'
        fstype = (part.get('fstype') or '').lower()
        mode = effective_recovery_mode(recovery_request, fstype)
        print(f'\n--- {label} {part["path"]} fs={fstype or "unknown"} carve={mode} ---')
        dst = folder / label
        dst.mkdir(mode=0o700)
        live_dir = dst / 'existing_files'
        recovered = dst / 'deleted_recovered'
        recovered.mkdir(mode=0o700)
        mount_dir = dst / '_readonly_mount'
        mount_dir.mkdir(mode=0o700)
        item = {'partition': part['path'], 'filesystem': fstype,
                'recovery_mode': mode, 'existing_copied_and_verified': False,
                'recovery_command_succeeded': False}
        results.append(item)
        # Existing-file copy occurs BEFORE carving as requested. Image is RO.
        mount_opts = 'ro,noload' if fstype in {'ext3', 'ext4'} else 'ro'
        proc = run(['mount', '-o', mount_opts, '--', part['path'], mount_dir],
                   check=False)
        if proc.returncode:
            raise RuntimeError(f'Cannot mount image {part["path"]} read-only; wipe blocked')
        try:
            item['existing'] = copy_existing(mount_dir, live_dir)
            item['existing_copied_and_verified'] = True
        finally:
            run(['umount', '--', mount_dir])
        mount_dir.rmdir()

        command = ['photorec', '/log', '/d', recovered, '/cmd', part['path'],
                   f'partition_none,fileopt,everything,enable,{mode},search']
        print('+', ' '.join(str(x) for x in command), flush=True)
        with (dst / 'photorec_output.txt').open('w') as out:
            result = subprocess.run([str(x) for x in command], cwd=dst,
                                    stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, check=False)
        item['photorec_exit_code'] = result.returncode
        if result.returncode != 0:
            raise RuntimeError(f'PhotoRec failed on {part["path"]}; see {dst / "photorec_output.txt"}')
        item['recovery'] = inventory_recovered(recovered)
        item['recovery_command_succeeded'] = True
    if not results:
        raise RuntimeError('No partition or filesystem found on disk image')
    return results


def save_report(folder, report):
    path = folder / 'report.json'
    pending = folder / 'report.json.tmp'
    with pending.open('w', encoding='utf-8') as fp:
        json.dump(report, fp, indent=2, default=str)
        fp.write('\n')
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(pending, path)


def wipe_zero_and_verify(original, folder, report, output, destination_mount):
    current = verify_source(original)
    if mounts(current):
        raise RuntimeError('USB was unexpectedly mounted before wipe; stopped')
    destination_safe(output, current, original=destination_mount)
    print(f'\nZERO-OVERWRITING {original["path"]} ({human_size(original["size"])})...')
    flags = os.O_RDWR | os.O_EXCL | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(original['path'], flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISBLK(st.st_mode) or st.st_rdev != original['devnum']:
            raise RuntimeError('Opened wipe device does not match selected USB')
        current = verify_source(original)
        if mounts(current):
            raise RuntimeError('USB became mounted during wipe preparation')
        destination_safe(output, current, original=destination_mount)
        # Exclusive block-device handle remains open for the entire wipe.
        report['wipe_started'] = dt.datetime.now().isoformat()
        save_report(folder, report)
        os.sync()  # Flush the archived data before any destructive write.
        print('Archive synced. Wipe authorized at initial disk selection.')
        zero_chunk = b'\x00' * CHUNK
        remaining = original['size']
        while remaining:
            n = min(CHUNK, remaining)
            data = memoryview(zero_chunk)[:n]
            while len(data):
                written = os.write(fd, data)
                if written <= 0:
                    raise RuntimeError('Zero write failed; USB may be partially wiped')
                data = data[written:]
            remaining -= n
        os.fsync(fd)
        if hasattr(os, 'posix_fadvise') and hasattr(os, 'POSIX_FADV_DONTNEED'):
            os.posix_fadvise(fd, 0, original['size'], os.POSIX_FADV_DONTNEED)
        os.lseek(fd, 0, os.SEEK_SET)
        remaining = original['size']
        while remaining:
            chunk = os.read(fd, min(CHUNK, remaining))
            if not chunk or chunk.count(0) != len(chunk):
                raise RuntimeError('Zero readback mismatch; USB may be partially wiped')
            remaining -= len(chunk)
        report['wipe_complete_and_readback_verified'] = True
        report['wipe_completed'] = dt.datetime.now().isoformat()
        save_report(folder, report)
        os.sync()
    finally:
        os.close(fd)
    print('USB zero-filled and readback verified. Drive is now unpartitioned.')


def load_configuration(path):
    if not path.is_file():
        raise RuntimeError(f'Configuration file not found: {path}\n'
                           'Install usb_archive.ini.example at that path and edit destination.')
    config = configparser.ConfigParser(interpolation=None)
    with path.open(encoding='utf-8') as fp:
        config.read_file(fp)
    if not config.has_section('archive'):
        raise RuntimeError('Configuration needs an [archive] section')
    directory = config.get('archive', 'destination', fallback='').strip()
    if not directory or not Path(directory).is_absolute():
        raise RuntimeError('Set [archive] destination to an absolute path')
    mode = config.get('archive', 'recovery_mode', fallback='auto').strip().lower()
    if mode not in {'auto', 'freespace', 'wholespace'}:
        raise RuntimeError('recovery_mode must be auto, freespace or wholespace')
    return Path(directory).resolve(), mode


def choose_usb(*, unattended=False, excluded=None):
    done = excluded or set()
    choices = []
    print('\nUSB disks (entire drives, not partitions):')
    for n in usb_disks():
        try:
            identity = disk_identity(n)
        except OSError:
            continue
        token = (identity['path'], identity['diskseq'], identity['devnum'])
        if token in done:
            continue
        choices.append((n, identity))
        print(f'  {n["path"]:<14} {human_size(n.get("size")):<12} '
              f'{(n.get("model") or "").strip():<28} '
              f'serial={(n.get("serial") or "unknown").strip()} '
              f'diskseq={identity["diskseq"] or "unavailable"}')
    if not choices:
        print('No unprocessed USB disks detected.')
        return None
    if unattended:
        print('\nUNATTENDED MODE: Archive and verify, then ZERO the selected USB without further prompts.')
        print('Select a drive by typing exactly: WIPE /dev/sdX  (using its actual path above)')
        response = input('Drive to archive and ERASE (or q to quit): ').strip()
        if response.lower() == 'q':
            return None
        found = [pair for pair in choices if response == f'WIPE {pair[0]["path"]}']
        if len(found) != 1:
            print('No drive authorized. Use WIPE followed by the exact full disk path.')
            return None
        return found[0]
    response = input('Select device path (e.g. /dev/sdb), or q to quit: ').strip()
    if response.lower() == 'q':
        return None
    found = [pair for pair in choices if response == pair[0]['path']]
    if len(found) != 1:
        print('Invalid drive selection.')
        return None
    return found[0]


def _process_usb(node, original, output, mode, unattended):
    current = verify_source(original)
    dest_mount = destination_safe(output, current)
    unmount_usb(original)
    current = verify_source(original)
    destination_safe(output, current, original=dest_mount)
    free_bytes = shutil.disk_usage(output).free
    needed = original['size'] * 2 + 256 * 1024 * 1024
    if free_bytes < needed:
        raise RuntimeError(f'Archive free space {human_size(free_bytes)}; need at least '
                           f'{human_size(needed)} (carving may need more)')
    if not unattended:
        print('Archive-only mode: selected drive WILL NOT be wiped automatically.')
        if input(f'Type ARCHIVE {original["path"]} to proceed: ').strip() != f'ARCHIVE {original["path"]}':
            print('Skipped.')
            return None
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    serial = re.sub(r'[^A-Za-z0-9_-]', '_', original['serial'] or 'noserial')[:32]
    folder = output / f'{stamp}_{Path(original["path"]).name}_{serial}'
    folder.mkdir(mode=0o700)
    report = {
        'started': dt.datetime.now().isoformat(),
        'source': {k: v for k, v in original.items() if k != 'devnum'},
        'archive_folder': str(folder),
        'recovery_mode_requested': mode,
        'unattended_auto_wipe': unattended,
        'image_verified': False,
        'archive_verified': False,
        'wipe_complete_and_readback_verified': False,
    }
    save_report(folder, report)
    loop = None
    try:
        current = verify_source(original)
        if mounts(current):
            raise RuntimeError('USB remounted before imaging; stopped')
        destination_safe(output, current, original=dest_mount)
        image_path = folder / 'drive.img'
        imaged_bytes = image_disk(original['path'], image_path, folder / 'ddrescue.map')
        if imaged_bytes != original['size'] or image_path.stat().st_size != original['size']:
            raise RuntimeError('Incomplete disk image; never wipe')
        print('Checking independent SHA-256 hashes of original disk and image...')
        source_hash = digest_file(original['path'])
        image_hash = digest_file(image_path)
        report['source_sha256'] = source_hash
        report['image_sha256'] = image_hash
        if source_hash != image_hash:
            raise RuntimeError('Disk and image hashes disagree; never wipe')
        report['image_verified'] = True
        save_report(folder, report)
        loop, parts = make_loop(image_path)
        report['partitions'] = process_partitions(parts, folder, mode)
    finally:
        if loop:
            run(['losetup', '--detach', loop])
    report['archive_verified'] = all(p['existing_copied_and_verified'] and
                                     p['recovery_command_succeeded']
                                     for p in report.get('partitions', []))
    report['archive_finished'] = dt.datetime.now().isoformat()
    save_report(folder, report)
    if not report['archive_verified']:
        raise RuntimeError('Archive incomplete; refusing to wipe')
    os.sync()
    current = verify_source(original)
    destination_safe(output, current, original=dest_mount)
    print(f'\nARCHIVE SUCCESSFUL: {folder}')
    if unattended:
        wipe_zero_and_verify(original, folder, report, output, dest_mount)
    else:
        print('Archive-only mode: source drive was NOT wiped.')
    return folder


def process_usb(node, original, output, mode, unattended):
    """Reserve destination storage while processing this source disk.

    The caller must hold reserve_source(original) through this entire call.
    """
    # First perform the existing distinct-device check; then lock destination
    # ancestors to prevent a second instance selecting this backup disk.
    current = verify_source(original)
    destination_safe(output, current)
    with reserve_destination(output):
        return _process_usb(node, original, output, mode, unattended)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=CONFIG_PATH,
                        help=f'INI file OUTSIDE repo (default {CONFIG_PATH})')
    parser.add_argument('--unattended', action='store_true',
                        help='After explicit WIPE /dev/sdX selection: copy, carve, then zero + verify, no more prompts')
    parser.add_argument('--output', type=Path, help='Override archive destination from config')
    parser.add_argument('--recovery-mode', choices=['auto', 'freespace', 'wholespace'],
                        help='Override PhotoRec scan mode from config')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run with sudo to access disks and use read-only mounts')
    for exe in ('lsblk', 'findmnt', 'ddrescue', 'losetup', 'udevadm',
                'mount', 'umount', 'photorec'):
        if shutil.which(exe) is None:
            parser.error(f'Missing {exe}; install gddrescue, testdisk and util-linux')
    try:
        output, mode = load_configuration(args.config)
        if args.output:
            output = args.output.resolve()
        if args.recovery_mode:
            mode = args.recovery_mode
        if not output.is_dir():
            parser.error(f'Archive directory does not exist: {output}')
        print('Archive destination:', output)
        print('Recovery mode:', mode)
        completed = set()
        while True:
            pair = choose_usb(unattended=args.unattended, excluded=completed)
            if pair is None:
                return 0
            node, original = pair
            # In unattended mode the exact typed drive-path selection is the
            # single explicit authorization to DESTROY all data on that disk.
            with reserve_source(original):
                folder = process_usb(node, original, output, mode, args.unattended)
            if folder is not None:
                completed.add((original['path'], original['diskseq'], original['devnum']))
            print('\nReady for next USB. Unplug the completed one if desired.')
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError,
            configparser.Error) as exc:
        print(f'\nSTOPPED ON ERROR: {exc}', file=sys.stderr)
        print('If wiping had started, the USB might be partially overwritten. '
              'Review the last report.json before any reuse.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nInterrupted. USB was NOT approved for wipe unless wipe had started.',
              file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
