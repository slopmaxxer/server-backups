#!/usr/bin/env python3
"""Consistent online SQLite snapshots plus trough uploads, with restore verification."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from urllib.parse import unquote, urlsplit

MEDIA_NAME = re.compile(r'^[0-9A-Z]{26}\.(jpg|png|gif)$')
ARCHIVE_NAME = re.compile(r'^services-\d{8}T\d{12}Z\.tar\.gz$')


def readonly_database(path: Path):
    return closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True))


def snapshot_database(source: Path, destination: Path, user: str | None) -> None:
    if user:
        # Open the live database as its own service user so WAL/SHM ownership
        # cannot change. The destination lives in that user's private staging dir.
        account = pwd.getpwnam(user)
        subprocess.run(['/usr/bin/sqlite3', '-cmd', '.timeout 10000', str(source),
                        f'.backup "{destination}"'], user=account.pw_uid, group=account.pw_gid,
                       extra_groups=[], check=True, timeout=60)
    else:
        # Fixture/test databases only. Production callers always provide a user.
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
    os.chmod(destination, 0o600)


def media_references(database: Path, host: str) -> set[str]:
    with readonly_database(database) as db:
        names = {row[0] for row in db.execute("SELECT file FROM attachments WHERE file != ''")}
        for avatar, header in db.execute('SELECT avatar, header FROM accounts'):
            for value in (avatar, header):
                parsed = urlsplit(value)
                if parsed.hostname == host and parsed.path.startswith('/media/'):
                    names.add(unquote(parsed.path.removeprefix('/media/')))
    if any(not MEDIA_NAME.fullmatch(name) for name in names):
        raise ValueError('Database contains an unexpected local media filename; refusing an incomplete backup.')
    return names


def copy_media(source: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    for path in source.iterdir():
        if path.is_symlink() or not path.is_file() or not MEDIA_NAME.fullmatch(path.name):
            raise ValueError(f'Unexpected entry in media directory: {path.name}')
        try:
            shutil.copy2(path, destination / path.name)
        except FileNotFoundError:
            # Deletion concurrent with the copy is okay only if the new snapshot
            # does not reference it. check_snapshot below decides that.
            continue


def check_snapshot(root: Path, host: str) -> dict:
    databases = {}
    for name, filename in (('trough', 'trough.sqlite3'), ('slopd', 'slopd.sqlite3')):
        path = root / name / filename
        with readonly_database(path) as db:
            checks = db.execute('PRAGMA quick_check').fetchall()
            if checks != [('ok',)]:
                raise ValueError(f'{name} database integrity check failed')
            version = db.execute('PRAGMA user_version').fetchone()[0]
        databases[name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'user_version': version}
    missing = media_references(root / 'trough/trough.sqlite3', host) - {
        path.name for path in (root / 'trough/media').iterdir() if path.is_file()
    }
    if missing:
        raise FileNotFoundError(f'Snapshot is missing {len(missing)} referenced media files')
    return {'databases': databases, 'media_files': len(list((root / 'trough/media').iterdir()))}


def create_snapshot(root: Path, trough: Path, slopd: Path, config: Path,
                    host: str, users: tuple[str | None, str | None]) -> dict:
    for name, user in zip(('trough', 'slopd'), users):
        folder = root / name
        folder.mkdir(mode=0o700)
        if user:
            account = pwd.getpwnam(user)
            os.chown(folder, account.pw_uid, account.pw_gid)
    snapshot_database(slopd / 'slopd.sqlite3', root / 'slopd/slopd.sqlite3', users[1])
    for attempt in range(3):
        snapshot_database(trough / 'trough.sqlite3', root / 'trough/trough.sqlite3', users[0])
        media = root / 'trough/media'
        if media.exists():
            shutil.rmtree(media)
        copy_media(trough / 'media', media)
        try:
            result = check_snapshot(root, host)
            break
        except FileNotFoundError:
            if attempt == 2:
                raise
            # A post/profile image may have been deleted after the DB snapshot.
            # Retry with a fresh DB and media copy, rather than saving broken refs.
    shutil.copyfile(config, root / 'trough/trough.env')
    os.chmod(root / 'trough/trough.env', 0o600)
    result.update(created_at=datetime.now(timezone.utc).isoformat(), trough_host=host, format_version=1)
    (root / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def verify_archive(archive: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix='slop-backup-verify-') as temp:
        root = Path(temp)
        with tarfile.open(archive, 'r:gz') as tar:
            # The data filter refuses paths and links escaping the restore dir.
            tar.extractall(root, filter='data')
        manifest = json.loads((root / 'manifest.json').read_text())
        actual = check_snapshot(root, manifest['trough_host'])
        if actual['databases'] != manifest['databases'] or actual['media_files'] != manifest['media_files']:
            raise ValueError('Archive verification does not match its manifest')
        if not (root / 'trough/trough.env').is_file():
            raise ValueError('Archive is missing the trough configuration')
        return manifest


def prune(output: Path, keep: int) -> None:
    archives = sorted(path for path in output.iterdir() if ARCHIVE_NAME.fullmatch(path.name) and path.is_file())
    for path in archives[:-keep]:
        path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/srv/backups/slop-services'))
    parser.add_argument('--keep', type=int, default=14, help='number of successful archives to retain')
    parser.add_argument('--verify', type=Path, help='extract and check an existing archive, without changing services')
    args = parser.parse_args()
    if args.verify:
        manifest = verify_archive(args.verify)
        print(f"Archive verified: {manifest['created_at']}; {manifest['media_files']} media files")
        return
    if os.geteuid() != 0:
        parser.error('run as root to back up the live service data')
    if args.keep < 1:
        parser.error('--keep must be at least 1')
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.output, 0o700)
    with open('/run/slop-services-backup.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='slop-services-backup-', dir='/var/tmp') as temp:
            root = Path(temp)
            os.chmod(root, 0o711)
            result = create_snapshot(root, Path('/var/lib/trough'), Path('/var/lib/slopd'),
                                     Path('/etc/trough.env'), 'fedi.freifick.net', ('trough', 'slopd'))
            name = 'services-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.tar.gz'
            final = args.output / name
            temporary = args.output / ('.' + name + '.tmp')
            try:
                with tarfile.open(temporary, 'w:gz') as tar:
                    for item in sorted(root.iterdir()):
                        tar.add(item, arcname=item.name)
                verify_archive(temporary)
                with temporary.open('rb') as handle:
                    os.fsync(handle.fileno())
                temporary.replace(final)
                directory_fd = os.open(args.output, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                temporary.unlink(missing_ok=True)
        prune(args.output, args.keep)
        print(f"Backup verified: {final}; {final.stat().st_size} bytes; {result['media_files']} media files")


if __name__ == '__main__':
    main()
