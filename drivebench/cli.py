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
            print(" DR I V E B E N C H  v0.4.2  |  Ubuntu drive workstation")
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
        except (UserError, inv.InventoryError, mounts.MountError, diagnostics.DiagnosticError, updates.UpdateError, processing.ProcessingError, raw_analysis.AnalysisError, ValueError, RuntimeError, OSError) as exc:
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
