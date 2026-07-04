#!/usr/bin/env bash
# Nightly consistent backup of the Dashin SQLite DB (run on the VPS host via cron).
# Uses SQLite's VACUUM INTO for a clean hot-copy (safe with WAL mode), then rotates.
#
# Cron (daily at 03:15):
#   15 3 * * *  /srv/dashin/deploy/backup.sh >> /var/log/dashin-backup.log 2>&1
set -euo pipefail

CONTAINER="${CONTAINER:-dashin}"
BACKUP_DIR="${BACKUP_DIR:-/srv/dashin-backups}"
KEEP="${KEEP:-14}"                 # keep this many most-recent backups
DB_IN_CONTAINER="/data/system/dashin.db"
TS="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR"

# Consistent copy inside the container, onto the mounted volume.
docker exec "$CONTAINER" python -c \
  "import sqlite3; sqlite3.connect('$DB_IN_CONTAINER').execute(\"VACUUM INTO '/data/system/_bak_tmp.db'\")"

docker cp "$CONTAINER:/data/system/_bak_tmp.db" "$BACKUP_DIR/dashin_$TS.db"
docker exec "$CONTAINER" rm -f /data/system/_bak_tmp.db

# Rotate — delete all but the newest $KEEP.
ls -1t "$BACKUP_DIR"/dashin_*.db 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

echo "$(date '+%F %T')  backup ok → $BACKUP_DIR/dashin_$TS.db  (keeping $KEEP)"

# OPTIONAL offsite copy — uncomment once rclone is configured (protects against
# the whole VPS dying). e.g. Backblaze B2, S3, Google Drive:
# rclone copy "$BACKUP_DIR/dashin_$TS.db" "remote:dashin-backups/" --quiet
