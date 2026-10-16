import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('backup', Path(__file__).resolve().parents[1] / 'backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
NAME = '01AAAAAAAAAAAAAAAAAAAAAAAA.jpg'


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trough = self.root / 'live-trough'
        self.slopd = self.root / 'live-slopd'
        (self.trough / 'media').mkdir(parents=True)
        self.slopd.mkdir()
        with closing(sqlite3.connect(self.trough / 'trough.sqlite3')) as db, db:
            db.executescript('CREATE TABLE attachments(file TEXT); CREATE TABLE accounts(avatar TEXT,header TEXT);')
            db.execute('INSERT INTO attachments VALUES (?)', (NAME,))
            db.execute('INSERT INTO accounts VALUES (?,?)', ('https://fedi.freifick.net/media/' + NAME, 'https://remote.test/media/not-local.png'))
        (self.trough / 'media' / NAME).write_bytes(b'fixture media')
        with closing(sqlite3.connect(self.slopd / 'slopd.sqlite3')) as db, db:
            db.execute('CREATE TABLE counters(name TEXT,value INTEGER)')
            db.execute("INSERT INTO counters VALUES ('visits',42)")
        self.config = self.root / 'trough.env'
        self.config.write_text('TROUGH_HOST=fedi.freifick.net\n')
        self.stage = self.root / 'stage'
        self.stage.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        return backup.create_snapshot(self.stage, self.trough, self.slopd, self.config,
                                      'fedi.freifick.net', (None, None))

    def archive(self):
        archive = self.root / 'fixture.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for item in self.stage.iterdir():
                tar.add(item, arcname=item.name)
        return archive

    def test_snapshot_archive_restore(self):
        manifest = self.snapshot()
        self.assertEqual(manifest['media_files'], 1)
        self.assertEqual(backup.verify_archive(self.archive()), manifest)
        with backup.readonly_database(self.stage / 'slopd/slopd.sqlite3') as db:
            self.assertEqual(db.execute("SELECT value FROM counters WHERE name='visits'").fetchone()[0], 42)
        self.assertEqual((self.stage / 'trough/trough.env').stat().st_mode & 0o777, 0o600)

    def test_wal_rows_included_without_changing_live_database(self):
        path = self.slopd / 'slopd.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("UPDATE counters SET value=99 WHERE name='visits'")
            db.commit()
            self.snapshot()
            with backup.readonly_database(self.stage / 'slopd/slopd.sqlite3') as snapshot:
                self.assertEqual(snapshot.execute('SELECT value FROM counters').fetchone()[0], 99)
            self.assertEqual(db.execute('SELECT value FROM counters').fetchone()[0], 99)

    def test_video_and_audio_uploads_included(self):
        names = ['01BBBBBBBBBBBBBBBBBBBBBBBB.mp4', '01CCCCCCCCCCCCCCCCCCCCCCCC.mp3']
        with closing(sqlite3.connect(self.trough / 'trough.sqlite3')) as db, db:
            db.executemany('INSERT INTO attachments VALUES (?)', [(n,) for n in names])
        for n in names:
            (self.trough / 'media' / n).write_bytes(b'fixture ' + n.encode())
        self.assertEqual(self.snapshot()['media_files'], 3)

    def test_missing_referenced_media_refuses_snapshot(self):
        (self.trough / 'media' / NAME).unlink()
        with self.assertRaises(FileNotFoundError):
            self.snapshot()
        self.assertFalse((self.stage / 'manifest.json').exists())

    def test_corrupt_archive_detected(self):
        self.snapshot()
        with closing(sqlite3.connect(self.stage / 'slopd/slopd.sqlite3')) as db, db:
            db.execute('UPDATE counters SET value=7')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            backup.verify_archive(self.archive())

    def test_media_symlinks_refused(self):
        (self.trough / 'media' / NAME).unlink()
        (self.trough / 'media' / NAME).symlink_to(self.config)
        with self.assertRaisesRegex(ValueError, 'Unexpected entry'):
            self.snapshot()

    def test_retention_only_prunes_owned_archive_pattern(self):
        output = self.root / 'archives'
        output.mkdir()
        names = [f'services-20261014T02000{i}000000Z.tar.gz' for i in range(4)]
        for name in names + ['unrelated.tar.gz', '.incomplete.tmp']:
            (output / name).touch()
        backup.prune(output, 2)
        self.assertEqual({path.name for path in output.iterdir()}, set(names[2:] + ['unrelated.tar.gz', '.incomplete.tmp']))


if __name__ == '__main__':
    unittest.main()
