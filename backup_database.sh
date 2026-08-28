#!/bin/bash
set -e

PROJECT_DIR="/opt/project-atlas"
BACKUP_REPO="$PROJECT_DIR/backups"
PASSPHRASE_FILE="/root/.project_atlas_backup_passphrase"

DATE=$(date +"%Y-%m-%d_%H-%M-%S")
TEMP_BACKUP="$PROJECT_DIR/data/backup_temp.db"
BACKUP_FILE="$BACKUP_REPO/database/project_atlas_$DATE.db"
ENCRYPTED_FILE="$BACKUP_FILE.gpg"

# Konsistentes SQLite-Backup erstellen
docker exec project-atlas python -c "
import sqlite3
source = sqlite3.connect('/app/data/project_atlas.db')
backup = sqlite3.connect('/app/data/backup_temp.db')
source.backup(backup)
backup.close()
source.close()
"

# Backup verschieben
mv "$TEMP_BACKUP" "$BACKUP_FILE"

# Backup verschlüsseln
gpg --batch --yes \
    --pinentry-mode loopback \
    --passphrase-file "$PASSPHRASE_FILE" \
    --symmetric \
    --cipher-algo AES256 \
    --output "$ENCRYPTED_FILE" \
    "$BACKUP_FILE"

# Unverschlüsselte Datei sofort löschen
rm -f "$BACKUP_FILE"

cd "$BACKUP_REPO"

# Bereits vorhandene unverschlüsselte Backups entfernen
rm -f database/*.db

# Verschlüsselte Backups älter als 30 Tage entfernen
find database -type f -name "project_atlas_*.db.gpg" -mtime +30 -delete

git add -A database
git commit -m "Encrypted database backup $DATE" || true
git push origin main

echo "Verschlüsseltes Backup erfolgreich: $ENCRYPTED_FILE"
