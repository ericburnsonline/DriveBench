"""Private, atomically-written configuration and persistent drive history."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def paths() -> tuple[Path, Path]:
    home = Path.home()
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "drivebench" / "config.json"
    state = Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state") / "drivebench" / "state.json"
    return cfg, state


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_config() -> dict:
    return {"schema_version": 1, "setup_complete": False, "ignored_drives": [], "ignored_slots": [], "ignored_controllers": []}


def default_state() -> dict:
    return {"schema_version": 1, "next_id": 1, "records": {}, "mounts": {}}


def _private_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def read_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Unable to read {path}: {exc}. File not replaced.") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RuntimeError(f"Unsupported configuration/state format: {path}. File not replaced.")
    return data


def _atomic_write(path: Path, value: dict) -> None:
    _private_parent(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-drivebench-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            os.fchmod(fp.fileno(), 0o600)
            json.dump(value, fp, indent=2, sort_keys=True)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
        # Make the rename durable on filesystems supporting directory fsync.
        try:
            dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def edit_json(path: Path, fallback: dict):
    _private_parent(path)
    lockfile = path.with_suffix(path.suffix + ".lock")
    fd = os.open(lockfile, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with os.fdopen(fd, "r+") as fp:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
            value = read_json(path, fallback)
            yield value
            _atomic_write(path, value)
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
    except BaseException:
        # with os.fdopen closes the fd if opened; callers receive the error.
        raise


def initialize() -> tuple[Path, Path, bool]:
    cfg_path, state_path = paths()
    first_run = not cfg_path.exists()
    with edit_json(cfg_path, default_config()):
        pass
    with edit_json(state_path, default_state()):
        pass
    return cfg_path, state_path, first_run


def read_config() -> dict:
    return read_json(paths()[0], default_config())


def read_state() -> dict:
    return read_json(paths()[1], default_state())


def update_config(updater) -> None:
    with edit_json(paths()[0], default_config()) as data:
        updater(data)


def _summary(disk: dict) -> dict:
    return {key: disk.get(key) for key in ("path", "model", "vendor", "serial", "serial_source", "wwn", "size", "tran", "controller_id", "slot", "confidence")}


def remember_disks(disks: list[dict]) -> dict[str, dict]:
    """Create/update records only for drives with unambiguous hardware identity.

    Merely finding a disk does NOT upgrade its tested/wiped status.
    """
    found: dict[str, dict] = {}
    with edit_json(paths()[1], default_state()) as state:
        records = state.setdefault("records", {})
        by_key = {rec["identity"]: rec for rec in records.values() if rec.get("identity")}
        for disk in disks:
            key = disk.get("identity")
            if not key:
                continue
            now = timestamp()
            record = by_key.get(key)
            if record is None:
                rid = f"DB-{state['next_id']:06d}"
                state["next_id"] += 1
                record = {"id": rid, "identity": key, "first_seen": now,
                          "last_seen": now, "seen_count": 0,
                          "status": "DISCOVERED", "operations": [], "notes": []}
                records[rid] = record
                by_key[key] = record
            record["last_seen"] = now
            record["seen_count"] = int(record.get("seen_count", 0)) + 1
            record["last_device"] = _summary(disk)
            found[disk["path"]] = {"id": record["id"], "status": record.get("status", "DISCOVERED")}
    return found


def _new_record(state: dict, disk: dict, *, manual: bool) -> dict:
    rid = f"DB-{state['next_id']:06d}"
    state['next_id'] += 1
    now = timestamp()
    rec = {'id': rid, 'identity': None if manual else disk['identity'],
           'identity_method': 'manual_confirmation_required' if manual else 'hardware',
           'first_seen': now, 'last_seen': now, 'seen_count': 1,
           'status': 'DISCOVERED', 'operations': [], 'notes': [],
           'last_device': _summary(disk), 'manual_serial': None,
           'asset_id': None, 'identification_updates': []}
    state.setdefault('records', {})[rid] = rec
    return rec


def save_identification(disk: dict, *, record_id: str | None,
                        manual_serial: str | None, asset_id: str | None) -> str:
    """Atomically save operator labels independently of verified device serials.

    Anonymous records are NEVER eligible for automatic post-reboot matching.
    """
    import re
    manual_serial = (manual_serial or '').strip() or None
    asset_id = (asset_id or '').strip() or None
    for label, value in (('Serial', manual_serial), ('Asset ID', asset_id)):
        if value and (len(value) > 100 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._:/#\-]*', value)):
            raise ValueError(f'{label}: use 1-100 plain printable identifier characters')
    if not disk.get('identity') and not (manual_serial or asset_id):
        raise ValueError('An unidentified disk needs a manual serial or asset ID')
    with edit_json(paths()[1], default_state()) as state:
        records = state.setdefault('records', {})
        if record_id:
            rec = records.get(record_id)
            if rec is None:
                raise ValueError('Record not found')
            if rec.get('identity') and rec['identity'] != disk.get('identity'):
                raise ValueError('Hardware identity does not match the selected record')
            if not rec.get('identity') and disk.get('identity'):
                raise ValueError('Cannot attach a verified disk to an anonymous record automatically')
        elif disk.get('identity'):
            rec = next((r for r in records.values() if r.get('identity') == disk['identity']), None)
            if rec is None:
                rec = _new_record(state, disk, manual=False)
        else:
            rec = _new_record(state, disk, manual=True)
        if asset_id and any(r['id'] != rec['id'] and str(r.get('asset_id') or '').lower() == asset_id.lower()
                            for r in records.values()):
            raise ValueError('Asset ID is already assigned to another record')
        rec['manual_serial'] = manual_serial
        rec['asset_id'] = asset_id
        rec.setdefault('identification_updates', []).append({
            'at': timestamp(), 'source': 'operator_entry_not_hardware_verified',
            'manual_serial': manual_serial, 'asset_id': asset_id,
            'disk_at_entry': _summary(disk),
        })
        rec['last_device'] = _summary(disk)
        rec['last_seen'] = timestamp()
        return rec['id']


def confirm_manual_attachment(disk: dict, record_id: str) -> dict:
    """Explicit operator association; never inferred from slot, name or user serial."""
    if disk.get('identity'):
        raise ValueError('This drive already has a hardware identity; use its hardware record')
    rec = read_state().get('records', {}).get(record_id)
    if not rec or rec.get('identity') or rec.get('identity_method') != 'manual_confirmation_required':
        raise ValueError('Not an anonymous manual-identification record')
    prior = rec.get('last_device') or {}
    if prior.get('size') != disk.get('size') or prior.get('model') != disk.get('model'):
        raise ValueError('Model or capacity differs from saved manual record. Cannot attach.')
    if not (rec.get('manual_serial') or rec.get('asset_id')):
        raise ValueError('Manual record has no identification label')
    return rec


def touch_manual_attachment(disk: dict, record_id: str) -> None:
    """Log an explicitly confirmed reattachment without promoting its assurance."""
    confirm_manual_attachment(disk, record_id)
    with edit_json(paths()[1], default_state()) as state:
        rec = state['records'].get(record_id)
        if not rec or rec.get('identity'):
            raise ValueError('Record changed; reselect')
        rec['last_seen'] = timestamp()
        rec['last_device'] = _summary(disk)
        rec['seen_count'] = int(rec.get('seen_count', 0)) + 1
        rec.setdefault('identification_updates', []).append({
            'at': timestamp(), 'source': 'operator_confirmed_reattachment',
            'disk_at_entry': _summary(disk)})


def add_note(record_id: str, note: str) -> None:
    with edit_json(paths()[1], default_state()) as state:
        record = state["records"].get(record_id)
        if not record:
            raise ValueError(f"Unknown record: {record_id}")
        record.setdefault("notes", []).append({"at": timestamp(), "text": note})


def add_exclusion(kind: str, value: str) -> None:
    if kind not in ("ignored_drives", "ignored_slots", "ignored_controllers"):
        raise ValueError("Invalid exclusion type")
    with edit_json(paths()[0], default_config()) as config:
        entries = config.setdefault(kind, [])
        if value not in entries:
            entries.append(value)


def remove_exclusion(kind: str, value: str) -> bool:
    removed = False
    def update(data):
        nonlocal removed
        values = data.setdefault(kind, [])
        if value in values:
            values.remove(value)
            removed = True
    update_config(update)
    return removed


def record_manual_wipe(record_id: str, method: str, passes: int, note: str) -> None:
    """Log an operator assertion only; no claim of software wipe/verification."""
    if not method.strip() or passes < 1 or passes > 100:
        raise ValueError("Specify a wipe method and passes between 1 and 100")
    with edit_json(paths()[1], default_state()) as state:
        record = state["records"].get(record_id)
        if not record:
            raise ValueError(f"Unknown record: {record_id}")
        record.setdefault("operations", []).append({
            "at": timestamp(), "operation": "manual_wipe_report",
            "source": "operator_only_not_verified_by_drivebench",
            "method": method, "passes": passes, "note": note,
            "verification": "NOT VERIFIED BY DRIVEBENCH",
        })
        record["status"] = "MANUAL_WIPE_REPORTED"


def record_mount(mountpoint: str, device: str, majmin: str, record_id: str | None, identity: str | None, fstype: str, mount_id: str, boot_id: str) -> None:
    with edit_json(paths()[1], default_state()) as state:
        state.setdefault("mounts", {})[mountpoint] = {
            "mountpoint": mountpoint, "device": device, "majmin": majmin,
            "record_id": record_id, "identity": identity, "fstype": fstype,
            "mounted_at": timestamp(), "unmounted_at": None,
            "mount_id": mount_id, "boot_id": boot_id,
        }


def record_unmount(mountpoint: str) -> None:
    with edit_json(paths()[1], default_state()) as state:
        record = state.setdefault("mounts", {}).get(mountpoint)
        if record:
            record["unmounted_at"] = timestamp()


def start_processing(disk: dict, operation: str, fields: dict) -> dict:
    """Persist IN_PROGRESS before any block-device modification.

    Anonymous events are stored but NEVER implicitly reattached to a drive
    after a reboot. Slot and /dev name are not proof of drive identity.
    """
    if operation not in ('wipe', 'format', 'report'):
        raise ValueError('Unsupported processing operation')
    event_id = 'OP-' + __import__('uuid').uuid4().hex
    item = {'event_id': event_id, 'operation': operation, 'started_at': timestamp(),
            'outcome': 'IN_PROGRESS', 'device_at_start': disk['path'],
            'disk_summary': _summary(disk), **fields}
    with edit_json(paths()[1], default_state()) as state:
        records = state.setdefault('records', {})
        record_id = None
        if disk.get('identity'):
            for rid, record in state.get('records', {}).items():
                if record.get('identity') == disk['identity']:
                    record_id = rid
                    record.setdefault('operations', []).append(item)
                    record['status'] = operation.upper() + '_IN_PROGRESS'
                    break
        if not record_id and disk.get('manual_record_id') and disk.get('operator_bound_session'):
            rid = disk['manual_record_id']
            rec = records.get(rid)
            if (rec and not rec.get('identity') and
                    rec.get('identity_method') == 'manual_confirmation_required' and
                    (rec.get('last_device') or {}).get('size') == disk.get('size') and
                    (rec.get('last_device') or {}).get('model') == disk.get('model')):
                record_id = rid
                item['identity_assurance'] = 'operator_confirmed_for_this_session_only'
                rec.setdefault('operations', []).append(item)
                rec['status'] = operation.upper() + '_IN_PROGRESS'
        if not record_id:
            state.setdefault('unlinked_operations', {})[event_id] = item
    return {'event_id': event_id, 'record_id': record_id}


def _find_processing(state: dict, op: dict) -> tuple[dict, dict | None]:
    rid = op.get('record_id')
    record = state.get('records', {}).get(rid) if rid else None
    if record:
        event = next((x for x in record.get('operations', []) if x.get('event_id') == op['event_id']), None)
    else:
        event = state.get('unlinked_operations', {}).get(op['event_id'])
    if event is None:
        raise ValueError('Processing event missing; do not assume completion')
    return event, record


def update_processing(op: dict, fields: dict) -> None:
    with edit_json(paths()[1], default_state()) as state:
        event, _ = _find_processing(state, op)
        event.update(fields)


def finish_processing(op: dict, *, success: bool, fields: dict | None = None) -> None:
    with edit_json(paths()[1], default_state()) as state:
        event, record = _find_processing(state, op)
        event.update(fields or {})
        event['finished_at'] = timestamp()
        event['outcome'] = 'COMPLETED' if success else 'INCOMPLETE_OR_FAILED'
        if record:
            if not success:
                record['status'] = 'PROCESSING_INCOMPLETE'
            elif event['operation'] == 'wipe':
                record['last_wiped_at'] = event['finished_at']
                record['status'] = 'WIPED'
            elif event['operation'] == 'format':
                record['last_formatted_at'] = event['finished_at']
                record['status'] = 'WIPED_AND_FORMATTED' if record.get('last_wiped_at') else 'FORMATTED_NOT_WIPED'
            else:
                record['last_report_written_at'] = event['finished_at']
                record['status'] = 'PREPARED' if record.get('last_wiped_at') and record.get('last_formatted_at') else 'REPORTED'


def start_analysis(disk: dict, kind: str, fields: dict | None = None) -> dict:
    """Durably begin an evidence scan or recovery before opening raw media.

    Anonymous disks do not get matched to prior events merely by path/slot.
    """
    import uuid
    event = {'event_id': 'SCAN-' + uuid.uuid4().hex,
             'kind': kind, 'started_at': timestamp(),
             'status': 'IN_PROGRESS', 'disk_at_start': _summary(disk),
             'details': fields or {}}
    with edit_json(paths()[1], default_state()) as state:
        rec = next((r for r in state.setdefault('records', {}).values()
                    if disk.get('identity') and r.get('identity') == disk['identity']), None)
        if not rec and disk.get('operator_bound_session') and disk.get('manual_record_id'):
            candidate = state['records'].get(disk['manual_record_id'])
            if (candidate and candidate.get('identity_method') == 'manual_confirmation_required'
                and (candidate.get('last_device') or {}).get('model') == disk.get('model')
                and (candidate.get('last_device') or {}).get('size') == disk.get('size')):
                rec = candidate
        if rec:
            rec.setdefault('raw_analysis', []).append(event)
        else:
            state.setdefault('unlinked_analysis', {})[event['event_id']] = event
    return {'event_id': event['event_id'], 'record_id': rec['id'] if rec else None}


def finish_analysis(ref: dict, *, report: dict, success: bool) -> None:
    """Atomically commit completed/incomplete results; no file payloads saved."""
    with edit_json(paths()[1], default_state()) as state:
        rid = ref.get('record_id')
        if rid:
            entry = next((v for v in state.get('records', {}).get(rid, {}).get('raw_analysis', [])
                          if v['event_id'] == ref['event_id']), None)
        else:
            entry = state.get('unlinked_analysis', {}).get(ref['event_id'])
        if entry is None:
            raise ValueError('Raw analysis event missing; cannot mark completion')
        entry['finished_at'] = timestamp()
        entry['status'] = 'COMPLETED' if success else 'INCOMPLETE_OR_FAILED'
        entry['result'] = report


def update_analysis(ref: dict, fields: dict) -> None:
    """Checkpoint progress without treating an interrupted scan as completed."""
    with edit_json(paths()[1], default_state()) as state:
        rid = ref.get('record_id')
        if rid:
            entry = next((v for v in state.get('records', {}).get(rid, {}).get('raw_analysis', [])
                          if v.get('event_id') == ref['event_id']), None)
        else:
            entry = state.get('unlinked_analysis', {}).get(ref['event_id'])
        if entry is None:
            raise ValueError('Analysis event missing; stop scan')
        if entry['status'] != 'IN_PROGRESS':
            raise ValueError('Analysis already finished')
        entry.setdefault('progress', {}).update(fields)
