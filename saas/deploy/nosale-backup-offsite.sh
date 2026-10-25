#!/usr/bin/env bash
# Dedicated NoSale encrypted snapshots; never modifies WB, AI, or payment APIs.
set -euo pipefail
umask 077

: "${NOSALE_OFFSITE_HOST:?}"
: "${NOSALE_OFFSITE_USER:?}"
: "${NOSALE_OFFSITE_SSH_KEY:?}"
: "${NOSALE_OFFSITE_REMOTE_ROOT:?}"
: "${NOSALE_OFFSITE_GPG_RECIPIENT:?}"
LOCAL_ROOT="${NOSALE_BACKUP_LOCAL_ROOT:-/srv/mpnosale-saas/secure-backups/offsite}"
ENV_SOURCE="${NOSALE_PRIVATE_ENV:-/srv/mpnosale-saas/source/saas/.env}"
DB_CONTAINER="${NOSALE_DB_CONTAINER:-mpnosale-saas-db-1}"
GPG_HOME="${NOSALE_OFFSITE_GPG_HOME:-/etc/nosale-saas/gpg-public}"
REMOTE="${NOSALE_OFFSITE_USER}@${NOSALE_OFFSITE_HOST}"

[[ "$NOSALE_OFFSITE_HOST" =~ ^[a-zA-Z0-9.-]+$ ]]
[[ "$NOSALE_OFFSITE_USER" =~ ^[a-zA-Z0-9_-]+$ ]]
[[ "$NOSALE_OFFSITE_REMOTE_ROOT" =~ ^/[-a-zA-Z0-9_/.]+$ ]]
[[ "$DB_CONTAINER" =~ ^[a-zA-Z0-9_-]+$ ]]
[[ "$NOSALE_OFFSITE_GPG_RECIPIENT" =~ ^[0-9A-F]{40}$ ]]
test "$(id -u)" -eq 0
test -r "$NOSALE_OFFSITE_SSH_KEY"
test -s "$ENV_SOURCE"
test -d "$GPG_HOME"

# The production machine must hold only the recovery PUBLIC key. The secret
# decryption key stays on a separate recovery host, never on E5 or offsite.
if gpg --homedir "$GPG_HOME" --batch --list-secret-keys --with-colons 2>/dev/null | grep -q '^sec:'; then
    echo "Refusing backup: secret decryption key present on production" >&2
    exit 1
fi
actual_fpr="$(gpg --homedir "$GPG_HOME" --batch --with-colons --list-keys "$NOSALE_OFFSITE_GPG_RECIPIENT" 2>/dev/null | awk -F: '$1=="fpr" {print $10; exit}')"
test "$actual_fpr" = "$NOSALE_OFFSITE_GPG_RECIPIENT"

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
GPG=(gpg --homedir "$GPG_HOME" --batch --yes --no-tty --trust-model always
     --recipient "$NOSALE_OFFSITE_GPG_RECIPIENT" --encrypt)

# Stream directly into encryption; neither dump nor private SaaS env is stored
# in plaintext in the E5 snapshot directory, tarballs, or the offsite archive.
docker exec "$DB_CONTAINER" sh -lc 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' |
    "${GPG[@]}" --output "$SNAP/postgres.dump.gpg"
test -s "$SNAP/postgres.dump.gpg"
"${GPG[@]}" --output "$SNAP/saas.env.gpg" "$ENV_SOURCE"
test -s "$SNAP/saas.env.gpg"
printf 'format=nosale-openpgp-v1\nrecipient=%s\n' "$NOSALE_OFFSITE_GPG_RECIPIENT" > "$SNAP/FORMAT"
(
    cd "$SNAP"
    sha256sum postgres.dump.gpg saas.env.gpg FORMAT > SHA256SUMS
    sha256sum -c SHA256SUMS > /dev/null
    test ! -e postgres.dump
    test ! -e saas.env
)

"${SSH[@]}" "$REMOTE" "umask 077; mkdir -p '$NOSALE_OFFSITE_REMOTE_ROOT'; chmod 700 '$NOSALE_OFFSITE_REMOTE_ROOT'; mkdir -p '$REMOTE_PARTIAL'; chmod 700 '$REMOTE_PARTIAL'"
rsync -az --partial -e "ssh -i $NOSALE_OFFSITE_SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10" "$SNAP/" "$REMOTE:$REMOTE_PARTIAL/"
"${SSH[@]}" "$REMOTE" "set -e; cd '$REMOTE_PARTIAL'; test -f postgres.dump.gpg; test -f saas.env.gpg; test -f FORMAT; test ! -e postgres.dump; test ! -e saas.env; sha256sum -c SHA256SUMS >/dev/null; if test -e '$REMOTE_FINAL'; then cd '$REMOTE_FINAL'; sha256sum -c SHA256SUMS >/dev/null; rm -rf '$REMOTE_PARTIAL'; else mv '$REMOTE_PARTIAL' '$REMOTE_FINAL'; fi; find '$NOSALE_OFFSITE_REMOTE_ROOT' -mindepth 1 -maxdepth 1 -type d -name '20*T*Z' -mtime +20 -exec rm -rf -- {} +"
find "$LOCAL_ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*T*Z' -mtime +6 -exec rm -rf -- {} +
echo "NOSALE_OFFSITE_ENCRYPTED_VERIFIED=$STAMP"
