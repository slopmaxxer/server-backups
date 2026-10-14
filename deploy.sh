#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
python3 -m unittest discover -s tests -v
sudo install -Dm644 backup.py /usr/local/lib/slop-backups/backup.py
sudo install -Dm644 systemd/slop-services-backup.service /etc/systemd/system/slop-services-backup.service
sudo install -Dm644 systemd/slop-services-backup.timer /etc/systemd/system/slop-services-backup.timer
sudo systemd-analyze verify /etc/systemd/system/slop-services-backup.service /etc/systemd/system/slop-services-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now slop-services-backup.timer
sudo systemctl start slop-services-backup.service
