"""DriveBench 0.4: interactive terminal menus only; no command-line subcommands."""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from . import deps, inventory as inv, mounts, store, diagnostics, updates, processing


class UserError(RuntimeError):
    pass


def status_reasons(disk: dict, config: dict) -> list[str]:
    reasons = []
    for reason in disk.get("system_reasons", []):
        reasons.append("SYSTEM: " + reason)
    if disk.get("identity") and disk["identity"] in config.get("ignored_drives", []):
        reasons.append("IGNORED: drive identity")
    if disk.get("slot") and disk["slot"] in config.get("ignored_slots", []):
        reasons.append("IGNORED: connection slot")
    if disk.get("controller_id") and disk["controller_id"] in config.get("ignored_controllers", []):
        reasons.append("IGNORED: SCSI/RAID controller")
    if disk.get("readonly"):
        reasons.append("READ-ONLY device")
    if disk.get("mountpoints"):
        reasons.append("MOUNTED: " + ", ".join(disk["mountpoints"]))
    if disk.get("confidence") != "strong":
        reasons.append("IDENTITY UNVERIFIED: manual association required after each restart" if
                       disk.get('manual_record_id') else "IDENTITY UNVERIFIED: history not linked")
    return reasons


def snapshot(*, sudo_probe_path: str | None = None) -> dict:
    result = inv.discover(sudo_probe_path=sudo_probe_path) if sudo_probe_path else inv.discover()
    config = store.read_config()
    seen = store.remember_disks(result["disks"])
    for disk in result["disks"]:
        entry = seen.get(disk["path"], {})
        disk["record_id"] = entry.get("id")
        disk["status"] = entry.get("status", "UNVERIFIED")
        if not disk.get('identity'):
            binding = _SESSION_MANUAL_LINKS.get(_session_key(disk))
            if binding:
                try:
                    rec = store.confirm_manual_attachment(disk, binding)
                    disk['record_id'] = rec['id']
                    disk['manual_record_id'] = rec['id']
                    disk['operator_bound_session'] = True
                    disk['status'] = rec.get('status', 'DISCOVERED')
                except ValueError:
                    _SESSION_MANUAL_LINKS.pop(_session_key(disk), None)
        disk["reasons"] = status_reasons(disk, config)
        disk.pop("tree", None)
    for ctl in result["controllers"]:
        ctl["ignored"] = ctl["id"] in config.get("ignored_controllers", [])
    return result


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _pick(items: list, heading: str, describe) -> tuple[int, object] | None:
    print("\n" + heading)
    for i, item in enumerate(items, 1):
        print(f" {i:>2}. {describe(item)}")
    if not items:
        print("  (none)")
        return None
    print("  0. Back")
    raw = _ask("Selection: ")
    if raw in ("", "0"):
        return None
    if not raw.isdecimal() or not (1 <= int(raw) <= len(items)):
        raise UserError("Select a displayed number")
    index = int(raw) - 1
    return index, items[index]


def _disk_desc(disk: dict) -> str:
    marks = []
    if any(r.startswith("SYSTEM:") for r in disk["reasons"]):
        marks.append("SYSTEM")
    if any(r.startswith("IGNORED:") for r in disk["reasons"]):
        marks.append("IGNORED")
    return (f"{disk['path']}  {inv.sizestr(disk['size'])}  {disk['model'] or 'unknown'}  "
            f"{disk['record_id'] or 'NO STABLE ID'}  [{disk['status']}]"
            + ("  (" + ", ".join(marks) + ")" if marks else ""))


def _part_desc(part: dict) -> str:
    mounts_here = ", ".join(part.get("mountpoints") or []) or "not mounted"
    return (f"{part['path']}  {inv.sizestr(part['size'])} "
            f"{part.get('fstype') or 'unknown FS'}  [{mounts_here}]")


def _show_disks(result: dict) -> None:
    print("\nDISKS AND PARTITIONS")
    for warning in result["warnings"]:
        print(" WARNING:", warning)
    if not result["disks"]:
        print(" No disks found")
    for i, disk in enumerate(result["disks"], 1):
        print(f" {i}. {_disk_desc(disk)}")
        print(f"    Interface: {disk['tran']}  Hardware serial: {disk['serial'] or 'n/a'} "
              f"({disk.get('serial_source') or 'unknown'})  SCSI address: {disk['hctl'] or 'n/a'}")
        if disk.get('record_id'):
            rec = store.read_state().get('records', {}).get(disk['record_id'], {})
            if rec.get('manual_serial') or rec.get('asset_id'):
                print(f"    Operator serial: {rec.get('manual_serial') or 'n/a'}; "
                      f"Asset ID: {rec.get('asset_id') or 'n/a'} "
                      "(operator supplied)")
        if disk["reasons"]:
            print("    " + "; ".join(disk["reasons"]))
        for part in disk["partitions"]:
            print("      - " + _part_desc(part))


def _show_controllers(result: dict) -> None:
    print("\nSCSI/SAS/RAID CONTROLLERS")
    if not result["controllers"]:
        print("  None detected (USB storage may still appear as SCSI).")
    for i, ctl in enumerate(result["controllers"], 1):
        print(f" {i}. {ctl['address']}  {ctl['label']}  "
              f"[{'IGNORED' if ctl['ignored'] else 'ENABLED'}]")
        print(f"    Hosts: {', '.join(ctl['hosts']) or 'none'}")


def _show_disk_details(disk: dict) -> None:
    details = {key: value for key, value in disk.items() if key != "tree"}
    record_id = disk.get('record_id')
    if record_id:
        record = store.read_state().get('records', {}).get(record_id, {})
        details['identification_record'] = {
            'inventory_id': record_id,
            'asset_id': record.get('asset_id'),
            'operator_supplied_serial': record.get('manual_serial'),
            'manual_values_are_hardware_verified': False,
            'record_binding': 'hardware' if disk.get('identity') else 'operator-confirmed-session',
        }
    print(json.dumps(details, indent=2, sort_keys=True))


def _add_exclusion_disk(disk: dict) -> None:
    modes = []
    if disk.get("identity"):
        modes.append(("ignored_drives", disk["identity"], "specific disk identity"))
    if disk.get("slot"):
        modes.append(("ignored_slots", disk["slot"], "connection slot (also excludes future disks there)"))
    picked = _pick(modes, "EXCLUDE BY", lambda m: m[2])
    if picked is None:
        if not modes:
            print("No trustworthy disk identity or slot found; exclusion unavailable for this disk.")
        return
    kind, value, name = picked[1]
    if _ask(f"Ignore {disk['path']} by {name}? Type YES: ") == "YES":
        store.add_exclusion(kind, value)
        print("Exclusion saved.")
    else:
        print("Cancelled.")


def _remove_exclusion() -> None:
    cfg = store.read_config()
    items = [(kind, value) for kind in ("ignored_drives", "ignored_slots", "ignored_controllers")
             for value in cfg.get(kind, [])]
    picked = _pick(items, "REMOVE EXCLUSION", lambda e: f"{e[0]}: {e[1]}")
    if picked is None:
        return
    kind, value = picked[1]
    if _ask("Remove this exclusion? Type YES: ") == "YES":
        store.remove_exclusion(kind, value)
        print("Exclusion removed.")


def _config_menu() -> None:
    while True:
        print("\nEXCLUSIONS / CONFIGURATION")
        print(" 1. Ignore a disk or physical slot")
        print(" 2. Ignore a SCSI/SAS/RAID controller")
        print(" 3. Remove an exclusion")
        print(" 4. Show saved configuration")
        print(" 0. Back")
        choice = _ask("Selection: ")
        if choice == "0":
            return
        if choice == "1":
            result = snapshot()
            selection = _pick(result["disks"], "CHOOSE DISK", _disk_desc)
            if selection:
                _add_exclusion_disk(selection[1])
        elif choice == "2":
            result = snapshot()
            selection = _pick(result["controllers"], "CHOOSE CONTROLLER", lambda c: f"{c['address']} {c['label']}")
            if selection:
                ctl = selection[1]
                if _ask(f"Ignore controller {ctl['address']}? Type YES: ") == "YES":
                    store.add_exclusion("ignored_controllers", ctl["id"])
                    print("Controller exclusion saved.")
        elif choice == "3":
            _remove_exclusion()
        elif choice == "4":
            cfg_path, state_path = store.paths()
            print(f"Configuration: {cfg_path}\nHistory: {state_path}")
            print(json.dumps(store.read_config(), indent=2))
        else:
            print("Unknown selection")


def _mount_partition_menu(disk: dict) -> None:
    if any(r.startswith("SYSTEM:") or r.startswith("IGNORED:") for r in disk["reasons"]):
        raise UserError("Cannot mount protected or excluded disk via DriveBench")
    part = [p for p in disk["partitions"] if p.get("type") == "part"]
    selection = _pick(part, "CHOOSE PARTITION TO MOUNT READ-ONLY", _part_desc)
    if selection:
        path = mounts.mount_partition(disk, selection[1])
        print("Mounted. In another terminal, browse/copy from: " + path)


def _filesystem_menu(disk: dict) -> None:
    print("\nThe inspection is read-only, but raw device probing may require sudo.")
    print("No signatures found DOES NOT prove a disk is blank.")
    priv = _ask("Try inspection using sudo? (y/N): ").lower() == 'y'
    print(diagnostics.filesystem_report(disk, sudo=priv))


def _health_menu(disk: dict) -> None:
    print("\nCollecting device-reported SMART/SCSI data (read-only).")
    print("A health report does not replace a full surface test.")
    priv = _ask("Use sudo for access to raw drive details? (y/N): ").lower() == 'y'
    sat = False
    if str(disk.get('tran', '')).lower() == 'usb':
        sat = _ask("Use SAT translation (-d sat) for USB bridge? (y/N): ").lower() == 'y'
    print(diagnostics.health_report(disk['path'], sudo=priv, usb_sat=sat))


def _network_update_menu() -> None:
    print("\nGITHUB UPDATE CHECK (download only, no automatic installation)")
    print("Repository: https://github.com/ericburnsonline/DriveBench")
    available = updates.branches()
    if not available:
        print("No publicly accessible GitHub branches found.")
        return
    local = updates.local_git_commit()
    print("Current checkout commit:", local[:12] if local else "unknown (ZIP/manual installation or Git unavailable)")
    chosen = _pick(available, "AVAILABLE GITHUB BRANCHES", lambda b: f"{b['name']}  ({b['sha'][:12]})")
    if not chosen:
        return
    branch = chosen[1]
    if local == branch['sha']:
        print("Your local Git checkout is already at this exact commit.")
        return
    if local:
        print("Local checkout differs from selected branch. This does not prove the branch is newer.")
    else:
        print("Local code version cannot be confirmed; GitHub branch state is available.")
    if _ask(f"Download ZIP for {branch['name']}@{branch['sha'][:12]} into /tmp? Type YES: ") != 'YES':
        print("No download performed.")
        return
    archive = updates.download(branch['sha'], branch['name'])
    diff = updates.changed_files(archive)
    print("\nSTAGED FILE COMPARISON (no changes applied):")
    for kind in ("added", "modified", "unchanged"):
        paths = diff[kind]
        print(f" {kind}: {len(paths)}")
        if kind != "unchanged":
            for path in paths[:30]:
                print("    " + path)
            if len(paths) > 30:
                print(f"    ...and {len(paths) - 30} more")
    if not diff['added'] and not diff['modified']:
        print("Installed files match the downloaded archive; no file changes are needed.")
    print("\n" + updates.upgrade_instructions(archive, branch['name']))


# Keep weak-identity processing evidence in memory only while a disk remains
# selected in this session. On reboot, it must not be assumed to be the same drive.
_SESSION_EVENTS: dict[tuple, list[str]] = {}
_SESSION_MANUAL_LINKS: dict[tuple, str] = {}


def _session_key(disk: dict) -> tuple:
    return (disk.get('path'), disk.get('size'), disk.get('model'),
            disk.get('serial'), disk.get('hctl'), disk.get('slot'), disk.get('diskseq'))


def _processing_menu(disk: dict) -> None:
    while True:
        print("\nDRIVE PREPARATION (DESTRUCTIVE)")
        print("Target:", _disk_desc(disk))
        print(" 1. Wipe entire physical disk (zero / random; 1-10 passes)")
        print(" 2. Create partition and format FAT32 or exFAT")
        print(" 3. Write drive information report to formatted partition")
        print(" 0. Back")
        choice = _ask("Selection: ")
        if choice == "0":
            return
        if choice not in ('1', '2', '3'):
            print('Unknown selection')
            continue
        current = next((d for d in snapshot()['disks'] if d['path'] == disk['path']), None)
        if current is None or not processing._same(disk, current):
            raise UserError('Drive identity changed. Return to drive list and rescan.')
        if choice == '1':
            print('Overwrite is destructive. It is NOT a certified SSD secure erase.')
            pattern = _pick(['zero', 'random'], 'OVERWRITE PATTERN', lambda x: x)
            if not pattern:
                continue
            raw = _ask('Number of passes (1-10, default 1): ') or '1'
            if not raw.isdecimal() or int(raw) not in range(1, 11):
                raise UserError('Passes must be 1-10')
            verify = _ask('Full post-wipe readback? (y/N): ').lower() == 'y'
            print('A readback after random overwrite checks readability, not exact content.')
            op = processing.wipe(current, pattern[1], int(raw), verify)
            _SESSION_EVENTS.setdefault(_session_key(disk), []).append(op['event_id'])
        elif choice == '2':
            default = 'fat32' if current['size'] <= 32 * 1024**3 else 'exfat'
            print('FAT32 / MBR: older systems, maximum single file 4 GiB minus 1 byte.')
            print('exFAT / GPT: modern Windows, macOS, Linux; not vintage Macintosh.')
            print('Suggested for this drive:', default)
            chosen = _pick(['fat32', 'exfat'], 'FORMAT TYPE', lambda x: x + (' (suggested)' if x == default else ''))
            if chosen:
                op = processing.format_drive(current, chosen[1])
                _SESSION_EVENTS.setdefault(_session_key(disk), []).append(op['event_id'])
                print('Format complete. Choose option 3 to write the report.')
        else:
            events = _SESSION_EVENTS.get(_session_key(disk), [])
            state = store.read_state()
            unlinked = [state.get('unlinked_operations', {}).get(i) for i in events]
            unlinked = [e for e in unlinked if e]
            processing.write_report(current, unlinked)


def _identification_menu(disk: dict) -> None:
    """Operator-supplied serials are durable metadata, NEVER hardware proof."""
    while True:
        current = next((d for d in snapshot()['disks'] if d['path'] == disk['path']), None)
        if not current or not processing._same(disk, current):
            raise UserError('Disk changed. Return to drive list and rescan.')
        rec = store.read_state().get('records', {}).get(current.get('record_id'), {})
        print('\nDRIVE IDENTIFICATION')
        print(' Device:', current['path'], current.get('model'), inv.sizestr(current['size']))
        print(' Detected hardware serial:', current.get('serial') or '(unavailable)',
              ' / source:', current.get('serial_source') or 'unavailable')
        print(' Hardware identity:', 'DEVICE-REPORTED' if current.get('identity') else 'UNVERIFIED')
        print(' Record:', current.get('record_id') or '(none)')
        print(' Operator-entered serial:', rec.get('manual_serial') or '(none)')
        print(' Asset ID:', rec.get('asset_id') or '(none)')
        print(' 1. Enter / edit manual serial and asset ID (saved immediately)')
        print(' 2. Retry automatic serial detection with sudo (read-only)')
        if not current.get('identity'):
            print(' 3. Reattach a previously saved manual record (requires confirmation)')
        print(' 0. Back')
        choice = _ask('Selection: ')
        if choice == '0':
            return
        if choice == '1':
            print('Manual values identify your physical label, not authenticated disk firmware.')
            print('Leave empty to clear a previously supplied value.')
            serial = _ask('Operator-supplied serial number: ')
            asset = _ask('Your asset / inventory label (optional): ')
            if not (serial or asset) and not current.get('identity'):
                raise UserError('Provide a serial or asset label for an unidentified disk')
            if _ask('Save this identification to persistent history? Type SAVE: ') != 'SAVE':
                print('Cancelled')
                continue
            record_id = store.save_identification(current,
                  record_id=current.get('record_id'), manual_serial=serial, asset_id=asset)
            if not current.get('identity'):
                _SESSION_MANUAL_LINKS[_session_key(current)] = record_id
            print(f'Saved as {record_id}. Your entry survives power loss; '
                  'unverified disks must be manually reattached after a reboot.')
            return
        if choice == '2':
            print('Trying smartctl / SCSI VPD read-only inquiry with sudo...')
            refreshed = next((d for d in snapshot(sudo_probe_path=disk['path'])['disks']
                             if d['path'] == disk['path']), None)
            if not refreshed or refreshed.get('size') != disk.get('size') or refreshed.get('diskseq') != disk.get('diskseq'):
                raise UserError('Device changed during inquiry; stop and rescan')
            if refreshed.get('identity'):
                print('Detected hardware serial:', refreshed['serial'])
                print('Source:', refreshed.get('serial_source'))
                print('Hardware identity available; select this disk again to view its record.')
                return
            print('No trustworthy device-native serial found. Manual labeling is available.')
        elif choice == '3' and not current.get('identity'):
            saved = [r for r in store.read_state().get('records', {}).values()
                     if r.get('identity_method') == 'manual_confirmation_required'
                     and (r.get('last_device') or {}).get('model') == current.get('model')
                     and (r.get('last_device') or {}).get('size') == current.get('size')]
            choice_rec = _pick(saved, 'SELECT SAVED MANUAL RECORD',
                lambda r: f"{r['id']} serial={r.get('manual_serial') or '-'} "
                          f"asset={r.get('asset_id') or '-'} status={r.get('status')}")
            if not choice_rec:
                continue
            existing = store.confirm_manual_attachment(current, choice_rec[1]['id'])
            proof = existing.get('asset_id') or existing.get('manual_serial')
            print('Compare the physical disk label to the stored identification.')
            if _ask(f'Type the saved {"asset ID" if existing.get("asset_id") else "serial"} exactly to link: ') != proof:
                print('Identifier did not match; nothing linked.')
                continue
            if _ask('Confirm this is the SAME physical disk? Type LINK: ') != 'LINK':
                print('Cancelled')
                continue
            store.touch_manual_attachment(current, existing['id'])
            _SESSION_MANUAL_LINKS[_session_key(current)] = existing['id']
            print('Record linked for this running session only. Repeat confirmation after reboot.')
            return
        else:
            print('Unknown selection')


def _disk_menu(disk: dict) -> None:
    while True:
        print("\nDRIVE:", _disk_desc(disk))
        print(" 1. View full drive details")
        print(" 2. View partitions")
        print(" 3. Mount a partition read-only")
        print(" 4. Add disk exclusion")
        print(" 5. View history / add note / manual wipe report")
        print(" 6. Inspect filesystem / partition signatures (read-only)")
        print(" 7. Show SMART / SCSI drive-health details (read-only)")
        print(" 8. Show detailed SCSI log pages (if available, read-only)")
        print(" 9. Wipe / format / write drive report (DESTRUCTIVE)")
        print("10. Identify drive / manually enter serial and asset ID")
        print(" 0. Back")
        choice = _ask("Selection: ")
        if choice == "0":
            return
        if choice == "1":
            _show_disk_details(disk)
        elif choice == "2":
            for p in disk["partitions"]:
                print("  " + _part_desc(p))
            if not disk["partitions"]:
                print("No partitions discovered")
        elif choice == "3":
            # Refresh and match exact scan path + hardware identity + capacity to limit swaps.
            current = next((d for d in snapshot()["disks"] if d["path"] == disk["path"]), None)
            if (not current or current["identity"] != disk["identity"] or
                    current["size"] != disk["size"] or current["serial"] != disk["serial"]):
                raise UserError("Disk changed during selection; return to drive list and rescan")
            _mount_partition_menu(current)
        elif choice == "4":
            _add_exclusion_disk(disk)
            return
        elif choice == "5":
            _single_history_menu(disk.get("record_id"))
        elif choice in ("6", "7", "8"):
            current = next((d for d in snapshot()["disks"] if d["path"] == disk["path"]), None)
            if (not current or current["identity"] != disk["identity"] or
                    current["size"] != disk["size"] or current["serial"] != disk["serial"]):
                raise UserError("Disk changed during selection; return to drive list and rescan")
            if choice == "6":
                _filesystem_menu(current)
            elif choice == "7":
                _health_menu(current)
            else:
                priv = _ask("Use sudo for SCSI LOG SENSE reading? (y/N): ").lower() == 'y'
                print(diagnostics.scsi_log_report(current['path'], sudo=priv))
        elif choice == "10":
            _identification_menu(disk)
            # Identification can change hardware identity and history linkage.
            return
        elif choice == "9":
            _processing_menu(disk)
            # Layout, mount points and status may have changed. Return to scan.
            return
        else:
            print("Unknown selection")


def _show_mounts() -> None:
    print("\nDRIVEBENCH MOUNT HISTORY AND LIVE STATUS")
    entries = mounts.active_managed()
    if not entries:
        print(" No partitions mounted through DriveBench yet.")
    for i, entry in enumerate(entries, 1):
        print(f" {i:>2}. [{entry['status']}] {entry['device']} -> {entry['mountpoint']}")
        print(f"     {entry.get('record_id') or 'UNVERIFIED'}  FS: {entry['fstype']}"
              f"  First mounted: {entry['mounted_at']}")
    result = snapshot()
    outside = [(p["path"], m) for d in result["disks"] for p in d["partitions"]
               for m in p["mountpoints"] if m not in {e["mountpoint"] for e in entries}]
    if outside:
        print("\nOther existing mounts (not managed here; use system tools to unmount):")
        for dev, target in outside:
            print(f"    {dev} -> {target}")


def _unmount_menu() -> None:
    entries = [e for e in mounts.active_managed() if e["active"]]
    selected = _pick(entries, "UNMOUNT DRIVEBENCH PARTITION", lambda e: f"{e['device']} -> {e['mountpoint']}")
    if selected:
        mounts.unmount_partition(selected[1])
        print("Unmounted successfully.")


def _single_history_menu(record_id: str | None) -> None:
    if not record_id:
        print("No associated record. Use Identify Drive to create or reattach a manual record.")
        return
    while True:
        record = store.read_state()["records"].get(record_id)
        if not record:
            print("Record not found")
            return
        print(f"\nDRIVE {record_id} HISTORY  [{record['status']}]")
        print(" 1. View complete record")
        print(" 2. Add note")
        print(" 3. Record a wipe done outside DriveBench (operator report only)")
        print(f" Serial: hardware={record.get('last_device', {}).get('serial') or '-'} "
              f"operator={record.get('manual_serial') or '-'} asset={record.get('asset_id') or '-'}")
        print(" 0. Back")
        choice = _ask("Selection: ")
        if choice == "0":
            return
        if choice == "1":
            print(json.dumps(record, indent=2, sort_keys=True))
        elif choice == "2":
            note = _ask("Note: ")
            if note:
                store.add_note(record_id, note)
                print("Saved")
        elif choice == "3":
            print("This DOES NOT wipe or verify the disk. It records your statement only.")
            method = _ask("External wipe method: ")
            raw_passes = _ask("Number of passes: ")
            note = _ask("Optional note: ")
            if not raw_passes.isdecimal():
                raise UserError("Pass count must be a positive number")
            if _ask("Type REPORT to confirm external wipe statement: ") == "REPORT":
                store.record_manual_wipe(record_id, method, int(raw_passes), note)
                print("Recorded MANUAL_WIPE_REPORTED, NOT VERIFIED BY DRIVEBENCH")
        else:
            print("Unknown selection")


def _history_menu() -> None:
    records = sorted(store.read_state().get("records", {}).values(), key=lambda r: r["id"])
    selection = _pick(records, "PERSISTENT DRIVE HISTORY", lambda r: f"{r['id']} {r['status']} {r.get('last_device', {}).get('model') or '?'}")
    if selection:
        _single_history_menu(selection[1]["id"])


def _first_run() -> None:
    if store.read_config().get("setup_complete"):
        return
    print("\nFIRST-RUN SETUP")
    print("System disks are always protected. You can ignore other disks/controllers now or later.")
    _show_disks(snapshot())
    while _ask("Configure exclusions now? (y/N): ").lower() == "y":
        _config_menu()
        break
    store.update_config(lambda data: data.update(setup_complete=True))
    print("Setup saved. Mounts, status and exclusions remain available after reboot.")


def _usb_refresh() -> None:
    if shutil.which("udevadm"):
        try:
            inv.run(["udevadm", "settle", "--timeout=5"], timeout=7)
        except inv.InventoryError as exc:
            print("udev settle warning:", exc)
    else:
        print("udevadm missing. To install: sudo apt install udev")
    print("Refreshed recognized USB devices. This does not reset/re-enumerate a USB bridge;")
    print("if you replaced a disk behind an unchanged adapter, safely unplug and reconnect it.")
    _show_disks(snapshot())


def menu() -> None:
    while True:
        try:
            print("\n" + "=" * 58)
            print(" DR I V E B E N C H  v0.4.1  |  Ubuntu drive workstation")
            print("=" * 58)
            print(" 1. Scan disks and partitions")
            print(" 2. Select drive (inspect / mount / history)")
            print(" 3. List all mount points and mount status")
            print(" 4. Unmount a DriveBench partition")
            print(" 5. Refresh USB storage")
            print(" 6. List SCSI controllers")
            print(" 7. Manage disk/controller exclusions")
            print(" 8. Drive history")
            print(" 9. Check Ubuntu components / installation help")
            print("10. Check GitHub for branch updates / download ZIP")
            print(" 0. Exit")
            choice = _ask("Selection: ")
            if choice == "0":
                return
            if choice == "1":
                _show_disks(snapshot())
            elif choice == "2":
                result = snapshot()
                picked = _pick(result["disks"], "SELECT DRIVE", _disk_desc)
                if picked:
                    _disk_menu(picked[1])
            elif choice == "3":
                _show_mounts()
            elif choice == "4":
                _unmount_menu()
            elif choice == "5":
                _usb_refresh()
            elif choice == "6":
                _show_controllers(snapshot())
            elif choice == "7":
                _config_menu()
            elif choice == "8":
                _history_menu()
            elif choice == "9":
                deps.doctor()
            elif choice == "10":
                _network_update_menu()
            else:
                print("Unknown selection")
        except (UserError, inv.InventoryError, mounts.MountError, diagnostics.DiagnosticError, updates.UpdateError, processing.ProcessingError, ValueError, RuntimeError, OSError) as exc:
            print("\nERROR:", exc)
            if isinstance(exc, inv.InventoryError):
                print(deps.instructions())
        except (EOFError, KeyboardInterrupt):
            print("\nExit")
            return


def main(argv: list[str] | None = None) -> int:
    if argv:
        print("DriveBench is menu-only. Run: python3 -m drivebench", file=sys.stderr)
        return 2
    if os.geteuid() == 0:
        print("Run as your ordinary user, NOT with sudo. The mount menu elevates individual operations.", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("DriveBench requires an interactive terminal.", file=sys.stderr)
        return 2
    try:
        cfg, state, created = store.initialize()
        if created:
            print(f"Configuration initialized: {cfg}\nPersistent drive history: {state}")
        missing = deps.missing()
        if missing:
            print("\n" + deps.instructions(missing))
        if not shutil.which("lsblk"):
            print("Cannot start disk inventory until lsblk is installed.", file=sys.stderr)
            return 1
        _first_run()
        menu()
        return 0
    except (RuntimeError, OSError, inv.InventoryError, KeyboardInterrupt, EOFError) as exc:
        print("DriveBench error:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
