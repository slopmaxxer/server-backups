# server-backups

Daily, verified local backups of the two SQLite services on freifick.net:

- **trough**: a SQLite snapshot, uploaded media and `/etc/trough.env`.
- **slopd**: a SQLite snapshot containing the guestbook, visitor counter and global pot.

This does not copy the regenerable trough media
proxy cache. Source code and deployment files live in their GitHub repositories.

## Install / run

Requires Python 3.12+, sqlite3 and systemd. On this server:

```sh
./deploy.sh
```

This runs the fixture tests, installs the script/units, enables the daily timer and creates a first
backup. Backups run around 02:45–03:00 UTC; missed runs are caught up after boot. Both application
services remain running. The service runs as root to read both accounts' files, but opens each live
SQLite database as its own service user so WAL/SHM ownership stays correct.

```sh
sudo systemctl start slop-services-backup.service
systemctl list-timers slop-services-backup.timer
journalctl -u slop-services-backup.service
sudo python3 /usr/local/lib/slop-backups/backup.py --verify /srv/backups/slop-services/services-....tar.gz
```

Archives are stored in `/srv/backups/slop-services/` (directory mode 0700, archives 0600). The last
14 successful archives are retained. Archive names use UTC timestamps; retention only touches the
script's own filename pattern. `--output PATH` and `--keep N` override those defaults for manual runs.
One lock prevents concurrent backup runs.

These archives contain private account data and signing keys. They belong outside the public web
root and Git repositories. They are on the same disk as the server: copy them to storage you control
off this machine for protection against disk loss. No off-site destination is configured here.

## What is verified

SQLite's online backup API creates self-contained snapshots, including committed WAL data. The
script copies media and checks every local attachment/avatar/header reference against the copy.
If deletion races the snapshot, it retries with a fresh trough snapshot up to three times; it fails
instead of publishing a backup with missing references. Uploaded files are immutable in trough.
The two services are independent snapshots, rather than a cross-service transaction.

Before publication, the complete archive is extracted into a disposable directory, both databases
pass `PRAGMA quick_check`, DB SHA-256 digests match the manifest, referenced media exist, and the
trough configuration exists. A temporary archive is fsynced and renamed only after successful
verification. Failed backups leave previous successful archives in place. No backup content is
printed to the journal.

Archive layout:

```text
manifest.json
trough/trough.sqlite3
trough/trough.env
trough/media/...
slopd/slopd.sqlite3
```

## Restore

First verify the chosen archive with `--verify`. Extract it into a private temporary directory
using a tar implementation that rejects paths escaping the destination. Keep that directory
root-only. Before touching live data, take another backup and stop the timer and both services:

```sh
sudo systemctl stop slop-services-backup.timer
sudo systemctl stop trough slopd
```

Move the existing `/var/lib/trough/` and `/var/lib/slopd/` aside for rollback. Recreate both with their
original users and permissions (`trough:trough`, 0750; `slopd:slopd`, 0700). Install the restored DBs
with mode 0600 and the appropriate service ownership, and restore `trough/media/` with owner
`trough:trough` (directories 0750, files 0640). Do not reuse WAL/SHM sidecars from the old databases.
Restore `/etc/trough.env` as root, mode 0600, if the configuration needs recovery. Its host must
remain `fedi.freifick.net`: federation IDs cannot be moved to another host.

Start `trough` and `slopd`, inspect their logs, and check the website/API plus
`https://fedi.freifick.net/nodeinfo/2.0`. Re-enable the backup timer after a successful restore.
The backup and application Git repositories supply the service definitions and binaries/scripts.
The verification command never writes into live service directories.

## Development checks

```sh
python3 -m unittest discover -s tests -v
```

Tests cover archive extraction and validation, committed WAL data, missing referenced media,
manifest mismatches, symlink rejection and retention boundaries. Fixture data are synthetic.
A live backup was also created and independently verified on the server.

MIT license. https://github.com/slopmaxxer/server-backups

## Nix

`nix build` packages `slop-services-backup`, including Python and SQLite, and
runs the synthetic fixture suite. `nix flake check` builds this check;
`nix develop` provides Python/SQLite. Use `nix run . -- --help` for CLI options.
Supported systems: x86_64-linux and aarch64-linux.

Import `inputs.server-backups.nixosModules.default` and enable
`services.slop-services-backup.enable`. Options `package`, `output`, `keep` and
`calendar` control the executable, archive directory, retention and schedule.
The root timer preserves service UID database access and requires the existing
trough/slopd users, databases, media and `/etc/trough.env` described above. It does
not provision those applications. Existing Arch deployment uses `./deploy.sh`.
