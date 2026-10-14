# server-backups development notes

Verified online SQLite/upload backups for this Arch server. `AGENTS.md` points here. Keep this file
and README up to date whenever backup behavior, paths, service units or restore procedures change.

- `python3 -m unittest discover -s tests -v` uses synthetic fixture DBs/media. `./deploy.sh` tests,
  installs `/usr/local/lib/slop-backups/backup.py` plus systemd units, enables the timer and takes a backup.
- Live databases: `/var/lib/trough/trough.sqlite3` (UID trough) and `/var/lib/slopd/slopd.sqlite3`
  (UID slopd). Always open them as their own service UIDs; preserve WAL/SHM ownership. Never print keys,
  credentials, user posts, notes or database contents while verifying backups.
- Include trough uploads + `/etc/trough.env`; exclude disposable media proxy cache. Use online backup,
  check media references, retry deletion races, extract/verify archives before atomic publication.
- Archives: `/srv/backups/slop-services/`, directory 0700 and files 0600, root-owned. Retain 14 successful
  archives; restrict pruning to the exact owned name pattern. Keep failed runs from destroying old backups.
- Timer `slop-services-backup.timer`: 02:45 UTC plus up to 15min delay, persistent. Root service uses
  no network and never stops the applications. GitLab has a separate backup timer.
- `sudo python3 /usr/local/lib/slop-backups/backup.py --verify <archive>` is read-only toward live data.
  README has restore instructions. No off-site destination is configured; these are same-disk copies.
- Never commit archives, extracted data or real secrets. Keep the repository public MIT, commits signed
  and pushed, and check status before touching concurrent work.
- Use small commits: one logical change per commit (with its tests/docs), each passing the checks above.
  Don't bundle unrelated changes or a whole session's work into one commit.
