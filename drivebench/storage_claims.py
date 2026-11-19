
"""Read-only Linux storage-claim diagnostics for a selected DriveBench disk.

This module never authorizes writing, unmounts, changes multipath mappings, or
starts/stops services. Its result is a snapshot, not a reservation or lock.
Destructive workflows must continue to use processing.preflight().
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from . import inventory, mounts

_SYS_BLOCK = Path('/sys/class/block')
_MOUNTINFO = Path('/proc/self/mountinfo')
_SWAPS = Path('/proc/swaps')
_BLOCK_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+!-]*\Z')


def _valid_name(name: str) -> bool:
    return bool(_BLOCK_NAME.fullmatch(name) and name not in ('.', '..'))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8', errors='replace').strip()
    except OSError:
        return ''


def _children(path: Path) -> list[str]:
    """Read a sysfs directory without following its entries to other devices."""
    return sorted(entry.name for entry in path.iterdir() if _valid_name(entry.name))


def inspect_claims(
    disk: dict,
    *,
    sys_block: Path = _SYS_BLOCK,
    mountinfo: Path = _MOUNTINFO,
    swaps: Path = _SWAPS,
) -> dict:
    """Describe mounts, swap, and kernel holders for one selected disk.

    All access is read-only. Any failed mandatory inspection is recorded and
    marks the snapshot blocked. Callers must independently revalidate the
    physical disk before a destructive action; this is *not* authorization.
    """
    device = str(disk.get('path') or '')
    kname = str(disk.get('kname') or '')
    result = {
        'device': device,
        'holders': [],
        'mounts': [],
        'swaps': [],
        'errors': [],
        'blocked': True,
    }
    if not _valid_name(kname) or device != f'/dev/{kname}':
        result['errors'].append('Selected disk lacks a valid /dev/<kernel-name> identity.')
        return result
    tree = disk.get('tree')
    if not isinstance(tree, dict) or str(tree.get('kname')) != kname:
        result['errors'].append('Selected disk tree does not match its kernel device name.')
        return result

    # Inventory supplies the selected disk and its partitions. We then follow
    # holders upwards (including chains such as sde -> dm-0 -> dm-1).
    selected: dict[str, str] = {}
    for node in inventory.flatten(tree):
        if node.get('type') not in ('disk', 'part'):
            continue
        name = str(node.get('kname') or '')
        if not _valid_name(name) or str(node.get('path')) != f'/dev/{name}':
            result['errors'].append('Disk tree has an invalid block-device entry.')
            continue
        selected[name] = str(node.get('maj:min') or '')
    if kname not in selected:
        result['errors'].append('Selected disk is absent from its own device tree.')
        return result

    examined: set[str] = set()
    queue = list(selected)
    all_nodes: dict[str, str] = {}
    relationships: set[tuple[str, str]] = set()
    while queue:
        name = queue.pop(0)
        if name in examined:
            continue
        examined.add(name)
        base = sys_block / name
        dev_number = _read(base / 'dev')
        if not dev_number:
            result['errors'].append(f'Cannot inspect kernel device number for {name}.')
            continue
        all_nodes[name] = dev_number
        if name in selected and selected[name] and selected[name] != dev_number:
            result['errors'].append(f'{name} kernel device number changed; rescan.')
        if name == kname and disk.get('diskseq'):
            current_seq = _read(base / 'diskseq')
            if not current_seq or current_seq != str(disk['diskseq']):
                result['errors'].append(f'{name} disk sequence changed or cannot be checked; rescan.')
        holders_dir = base / 'holders'
        try:
            holder_names = _children(holders_dir)
        except OSError as exc:
            result['errors'].append(f'Cannot inspect holders for {name}: {exc}.')
            continue
        for holder in holder_names:
            if (name, holder) not in relationships:
                relationships.add((name, holder))
                holder_base = sys_block / holder
                dm_name = _read(holder_base / 'dm' / 'name')
                dm_uuid = _read(holder_base / 'dm' / 'uuid')
                if dm_uuid.lower().startswith('mpath-'):
                    kind = 'multipath'
                elif holder.startswith('dm-'):
                    kind = 'device-mapper'
                elif holder.startswith('md'):
                    kind = 'software RAID'
                else:
                    kind = 'other block-device holder'
                try:
                    slaves = _children(holder_base / 'slaves')
                except OSError:
                    slaves = []
                result['holders'].append({
                    'source': f'/dev/{name}',
                    'device': f'/dev/{holder}',
                    'kind': kind,
                    'mapping_name': dm_name or None,
                    'mapping_uuid': dm_uuid or None,
                    'slaves': [f'/dev/{n}' for n in slaves],
                })
            if holder not in examined:
                queue.append(holder)

    # Compare kernel major:minor identifiers, not mount source strings alone:
    # a mount can refer to /dev/mapper/mpatha instead of /dev/dm-0.
    by_number = {value: name for name, value in all_nodes.items()}
    try:
        for entry in mounts.live_mounts(mountinfo):
            name = by_number.get(entry['majmin'])
            if name:
                result['mounts'].append({
                    'device': f'/dev/{name}',
                    'mountpoint': entry['mountpoint'],
                    'source': entry['source'],
                })
    except (OSError, ValueError, mounts.MountError) as exc:
        result['errors'].append(f'Cannot inspect kernel mount table: {exc}.')

    try:
        lines = swaps.read_text(encoding='utf-8', errors='replace').splitlines()
        # /proc/swaps includes a header. Resolve aliases such as /dev/mapper/...
        # to their kernel device names without opening or writing to devices.
        node_paths = {os.path.realpath(f'/dev/{name}'): name for name in all_nodes}
        for line in lines[1:]:
            fields = line.split()
            if fields:
                name = node_paths.get(os.path.realpath(fields[0]))
                if name:
                    result['swaps'].append({'device': f'/dev/{name}', 'source': fields[0]})
    except OSError as exc:
        result['errors'].append(f'Cannot inspect active swap: {exc}.')

    result['holders'].sort(key=lambda item: (item['source'], item['device']))
    result['mounts'].sort(key=lambda item: (item['device'], item['mountpoint']))
    result['swaps'].sort(key=lambda item: (item['device'], item['source']))
    result['blocked'] = bool(result['holders'] or result['mounts'] or result['swaps'] or result['errors'])
    return result


def describe_claims(report: dict) -> str:
    """Plain-language, read-only conflict summary for a future CLI screen."""
    lines = [f"Storage claims for {report.get('device') or 'unknown disk'}:"]
    for item in report.get('holders', []):
        name = f" ({item['mapping_name']})" if item.get('mapping_name') else ''
        lines.append(f"  HOLDER: {item['source']} -> {item['device']}{name} [{item['kind']}]")
        if item.get('slaves'):
            lines.append('    Backing devices: ' + ', '.join(item['slaves']))
    for item in report.get('mounts', []):
        lines.append(f"  MOUNTED: {item['device']} at {item['mountpoint']}")
    for item in report.get('swaps', []):
        lines.append(f"  ACTIVE SWAP: {item['source']} ({item['device']})")
    for message in report.get('errors', []):
        lines.append(f'  INSPECTION INCOMPLETE: {message}')
    if report.get('blocked', True):
        lines.append('Do not wipe or format. Inspect the specific holder/mount/swap; no automatic removal is attempted.')
    else:
        lines.append('No claims detected at inspection time. This does NOT reserve or authorize the disk.')
    return '\n'.join(lines)
