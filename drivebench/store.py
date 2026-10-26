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
