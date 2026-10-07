#!/usr/bin/env bash
# Nightly TradzLog database backup: compressed, kept 14 days in /var/backups/tradzlog, and copied
# to S3 when BACKUP_S3_BUCKET is set (Parameter Store /tradzlog/BACKUP_S3_BUCKET; Mizan's backup
# bucket works, under the tradzlog/ prefix). Installed by install.sh as /usr/local/bin/tradzlog-backup.
# Screenshots are already in S3 and are not part of this backup.
set -euo pipefail

dir=/var/backups/tradzlog
install -d -m 700 "$dir"
file="$dir/tradzlog-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"

docker compose --project-directory /srv/tradzlog exec -T postgres pg_dump -U tradzlog --clean --if-exists tradzlog | gzip >"$file.part"
mv "$file.part" "$file"
chmod 600 "$file"
find "$dir" -name 'tradzlog-*.sql.gz' -mtime +14 -delete

bucket="$(sed -n 's/^BACKUP_S3_BUCKET=//p' /srv/tradzlog/.env | tr -d "'\"")"
if [ -n "$bucket" ]; then
  /snap/bin/aws s3 cp "$file" "s3://$bucket/tradzlog/$(basename "$file")" --only-show-errors
fi
echo "$(date -u +%FT%TZ) backed up to $file"
