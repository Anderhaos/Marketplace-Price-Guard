#!/usr/bin/env bash
# Dedicated NoSale snapshot. Never requires or modifies WB, AI or payment APIs.
set -euo pipefail
umask 077

: "${NOSALE_OFFSITE_HOST:?}"
: "${NOSALE_OFFSITE_USER:?}"
: "${NOSALE_OFFSITE_SSH_KEY:?}"
: "${NOSALE_OFFSITE_REMOTE_ROOT:?}"
LOCAL_ROOT="${NOSALE_BACKUP_LOCAL_ROOT:-/srv/mpnosale-saas/secure-backups/offsite}"
ENV_SOURCE="${NOSALE_PRIVATE_ENV:-/srv/mpnosale-saas/source/saas/.env}"
DB_CONTAINER="${NOSALE_DB_CONTAINER:-mpnosale-saas-db-1}"
REMOTE="${NOSALE_OFFSITE_USER}@${NOSALE_OFFSITE_HOST}"

# These fields come exclusively from an E5 root-owned service EnvironmentFile.
[[ "$NOSALE_OFFSITE_HOST" =~ ^[a-zA-Z0-9.-]+$ ]]
[[ "$NOSALE_OFFSITE_USER" =~ ^[a-zA-Z0-9_-]+$ ]]
[[ "$NOSALE_OFFSITE_REMOTE_ROOT" =~ ^/[-a-zA-Z0-9_/.]+$ ]]
[[ "$DB_CONTAINER" =~ ^[a-zA-Z0-9_-]+$ ]]
test -r "$NOSALE_OFFSITE_SSH_KEY"
test -s "$ENV_SOURCE"
test "$(id -u)" -eq 0
mkdir -p "$LOCAL_ROOT"
chmod 700 "$LOCAL_ROOT"
exec 9>"$LOCAL_ROOT/.backup.lock"
flock -n 9 || { echo "NoSale backup already running" >&2; exit 1; }

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SNAP="$LOCAL_ROOT/$STAMP"
mkdir -m 700 "$SNAP"
SSH=(ssh -i "$NOSALE_OFFSITE_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10)
REMOTE_PARTIAL="$NOSALE_OFFSITE_REMOTE_ROOT/$STAMP.partial"
REMOTE_FINAL="$NOSALE_OFFSITE_REMOTE_ROOT/$STAMP"

# Dump uses DB's own restricted runtime identity, not a published port.
docker exec "$DB_CONTAINER" sh -lc 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >"$SNAP/postgres.dump"
test -s "$SNAP/postgres.dump"
pg_restore --list "$SNAP/postgres.dump" >/dev/null 2>&1 ||
    docker exec -i "$DB_CONTAINER" pg_restore -l <"$SNAP/postgres.dump" >/dev/null
# Complete disaster recovery also needs the existing protected encryption key,
# DB credentials and app config. Keep .env private in transit and at rest.
install -m 600 "$ENV_SOURCE" "$SNAP/saas.env"
(cd "$SNAP" && sha256sum postgres.dump saas.env > SHA256SUMS && sha256sum -c SHA256SUMS >/dev/null)

"${SSH[@]}" "$REMOTE" "umask 077; mkdir -p '$NOSALE_OFFSITE_REMOTE_ROOT'; chmod 700 '$NOSALE_OFFSITE_REMOTE_ROOT'; mkdir -p '$REMOTE_PARTIAL'; chmod 700 '$REMOTE_PARTIAL'"
rsync -az --partial -e "ssh -i $NOSALE_OFFSITE_SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10" "$SNAP/" "$REMOTE:$REMOTE_PARTIAL/"
"${SSH[@]}" "$REMOTE" "set -e; cd '$REMOTE_PARTIAL'; sha256sum -c SHA256SUMS >/dev/null; if test -e '$REMOTE_FINAL'; then cd '$REMOTE_FINAL'; sha256sum -c SHA256SUMS >/dev/null; rm -rf '$REMOTE_PARTIAL'; else mv '$REMOTE_PARTIAL' '$REMOTE_FINAL'; fi; find '$NOSALE_OFFSITE_REMOTE_ROOT' -mindepth 1 -maxdepth 1 -type d -name '20*T*Z' -mtime +20 -exec rm -rf -- {} +"
# Delete only dedicated completed snapshots, never arbitrary paths.
find "$LOCAL_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*T*Z' -mtime +6 -exec rm -rf -- {} +
echo "NOSALE_OFFSITE_VERIFIED=$STAMP"
