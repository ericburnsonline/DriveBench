"""Read-only GitHub update checking and ZIP staging; never modifies the installation."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

OWNER = 'ericburnsonline'
REPO = 'DriveBench'
API_ROOT = f'https://api.github.com/repos/{OWNER}/{REPO}'
SHA_PATTERN = re.compile(r'^[0-9a-fA-F]{40}$')


class UpdateError(RuntimeError):
    pass


def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'DriveBench-update-checker/0.3',
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.HTTPError) as exc:
        raise UpdateError(f'Cannot contact GitHub: {exc}') from exc


def branches() -> list[dict]:
    """List public branches, at most 500; don't rely on names being URL-safe."""
    found = []
    for page in range(1, 6):
        data = _get_json(f'{API_ROOT}/branches?per_page=100&page={page}')
        if not isinstance(data, list):
            raise UpdateError('Unexpected GitHub branch response')
        for item in data:
            if not isinstance(item, dict):
                continue
            sha = (item.get('commit') or {}).get('sha')
            name = item.get('name')
            if isinstance(name, str) and isinstance(sha, str) and SHA_PATTERN.fullmatch(sha):
                found.append({'name': name, 'sha': sha.lower()})
        if len(data) < 100:
            return found
    raise UpdateError('Repository contains over 500 branches; branch list truncated for safety.')


def local_git_commit() -> str | None:
    """Only identify current code when it is actually running inside a git checkout."""
    if not shutil.which('git'):
        return None
    repo_root = Path(__file__).resolve().parent.parent
    try:
        top = subprocess.run(['git', '-C', str(repo_root), 'rev-parse', '--show-toplevel'],
                             capture_output=True, text=True, timeout=5, check=False)
        if top.returncode or Path(top.stdout.strip()).resolve() != repo_root:
            return None
        result = subprocess.run(['git', '-C', str(repo_root), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, timeout=5, check=False)
        sha = result.stdout.strip().lower()
        return sha if result.returncode == 0 and SHA_PATTERN.fullmatch(sha) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def download(sha: str, branch: str, base: Path | None = None) -> Path:
    """Use wget to stage a content-addressed GitHub archive; no extraction or installation."""
    if not SHA_PATTERN.fullmatch(sha):
        raise UpdateError('Invalid commit SHA from GitHub')
    if not shutil.which('wget'):
        raise UpdateError('wget is missing. Install it with: sudo apt install wget')
    parent = base if base is not None else (Path(tempfile.gettempdir()) / 'drivebench-updates')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', branch).strip('._')[:40] or 'branch'
    dest = parent / f'drivebench-{safe}-{sha[:12]}.zip'
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    # Use a unique temp file in the same directory; never present partial downloads as complete.
    with tempfile.NamedTemporaryFile(prefix='.drivebench-', suffix='.zip', dir=parent, delete=False) as fp:
        partial = Path(fp.name)
    try:
        url = f'{API_ROOT}/zipball/{sha}'
        cp = subprocess.run(['wget', '--https-only', '--max-redirect=5', '--timeout=20',
                             '--tries=2', '-q', '-O', str(partial), url],
                            capture_output=True, text=True, timeout=90, check=False)
        if cp.returncode or partial.stat().st_size < 100:
            raise UpdateError('GitHub ZIP download failed. Check connectivity and permissions. '
                              + (cp.stderr or '').strip()[:300])
        # Validate archive structure before suggesting it for use.
        import zipfile
        if not zipfile.is_zipfile(partial):
            raise UpdateError('Downloaded file is not a valid ZIP archive')
        with zipfile.ZipFile(partial) as z:
            if 'drivebench/cli.py' not in '\n'.join(z.namelist()):
                raise UpdateError('Downloaded archive does not appear to contain DriveBench')
        partial.replace(dest)
        return dest
    finally:
        partial.unlink(missing_ok=True)


def upgrade_instructions(archive: Path, branch: str) -> str:
    return (f'ZIP staged at: {archive}\n'
            f'Source branch: {branch}\n'
            'NOTHING has been installed or replaced.\n\n'
            'For a Git clone with no uncommitted changes, you can update later using:\n'
            '  cd /path/to/DriveBench\n'
            '  git status\n'
            f'  git fetch origin\n'
            f'  git switch {branch!r}  # first time: git switch --track origin/{branch}\n'
            f'  git pull --ff-only origin {branch!r}\n'
            'To inspect the downloaded ZIP, use: unzip -l <zip-path>.\n'
            'Back up your installation before manually copying ZIP contents.\n'
            'Config/history are stored separately in ~/.config/drivebench/ and\n'
            '~/.local/state/drivebench/; keep both intact.\n'
            'Do not automatically replace code in a running DriveBench process.\n')


def changed_files(archive: Path, installed_root: Path | None = None) -> dict[str, list[str]]:
    """Compare staged ZIP file contents with locally installed code; do not write anything."""
    import zipfile
    root = installed_root or Path(__file__).resolve().parent.parent
    changes = {'added': [], 'modified': [], 'unchanged': []}
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            parts = Path(member.filename).parts
            if member.is_dir() or len(parts) < 2:
                continue
            rel = Path(*parts[1:])
            # Only ordinary relative paths inside the installation.
            if any(part in ('..', '.', '') for part in rel.parts) or rel.is_absolute():
                continue
            # Skip archive items we do not want to manage.
            if any(part in ('.git', '__pycache__') for part in rel.parts):
                continue
            local = root / rel
            if local.exists() and not local.is_file():
                changes['modified'].append(str(rel) + ' (local path not a regular file)')
            elif not local.exists():
                changes['added'].append(str(rel))
            else:
                with z.open(member) as src:
                    digest_remote = _hash_stream(src)
                with local.open('rb') as fp:
                    digest_local = _hash_stream(fp)
                changes['unchanged' if digest_remote == digest_local else 'modified'].append(str(rel))
    for values in changes.values():
        values.sort()
    return changes


def _hash_stream(fp) -> bytes:
    digest = hashlib.sha256()
    while True:
        chunk = fp.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.digest()
