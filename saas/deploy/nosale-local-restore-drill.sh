#!/usr/bin/env bash
# Restore ONE authorized local NoSale PG16 custom-format dump into an isolated,
# disposable PostgreSQL instance. Does not touch production, WB or offsite keys.
set -euo pipefail
umask 077

if [[ $# -ne 1 ]]; then
  echo "Usage: bash saas/deploy/nosale-local-restore-drill.sh /absolute/path/to/backup.dump" >&2
  exit 2
fi

SOURCE_INPUT="$1"
if [[ ! -f "$SOURCE_INPUT" || -L "$SOURCE_INPUT" || ! -s "$SOURCE_INPUT" ]]; then
  echo "Restore drill requires an existing nonempty regular (non-symlink) dump." >&2
  exit 2
fi
SOURCE="$(realpath -e -- "$SOURCE_INPUT")"
[[ "$SOURCE" = /* ]] || exit 2

# The tested image is already used by the SaaS Compose database.
IMAGE="postgres:16-alpine"
docker image inspect "$IMAGE" >/dev/null
echo "NOSALE_RESTORE_DRILL_BEGIN $(date -u +%FT%TZ)"

# - No network interface and no published port; never address the live DB.
# - Cluster and Unix socket exist exclusively in disposable container tmpfs.
# - The only host mount is an immutable, read-only source archive.
# - --rm destroys the restored private database and its temp files on exit.
docker run --rm --pull never --network none --pids-limit 128 \
  --memory 512m --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=256m \
  --tmpfs /tmp:rw,size=32m \
  --mount "type=bind,source=$SOURCE,target=/backup.dump,readonly" \
  --entrypoint sh "$IMAGE" -ec '
    set -eu
    chown postgres:postgres /var/lib/postgresql/data
    chmod 700 /var/lib/postgresql/data
    su-exec postgres initdb -D /var/lib/postgresql/data --auth-local=trust --no-instructions >/dev/null 2>&1
    su-exec postgres pg_ctl -D /var/lib/postgresql/data \
      -o "-c listen_addresses= -c unix_socket_directories=/tmp" \
      -l /tmp/postgres.log start >/dev/null
    su-exec postgres createdb -h /tmp drill
    # The archive is readable only inside this ephemeral container.
    if ! pg_restore -h /tmp -U postgres -d drill \
      --no-owner --no-privileges --exit-on-error /backup.dump 2>/tmp/restore-errors.log; then
      echo "RESTORE_FAILED: PostgreSQL rejected the archive" >&2
      exit 1
    fi
    # Count schemas/metadata only: NEVER emit seller records or credentials.
    tables=$(psql -h /tmp -U postgres -d drill -Atq \
      -c "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = '\''public'\''")
    accounts=$(psql -h /tmp -U postgres -d drill -Atq \
      -c "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = '\''public'\'' AND tablename = '\''guard_guardaccount'\''")
    finance=$(psql -h /tmp -U postgres -d drill -Atq \
      -c "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = '\''public'\'' AND tablename = '\''guard_financesnapshot'\''")
    migration=$(psql -h /tmp -U postgres -d drill -Atq \
      -c "SELECT COUNT(*) FROM django_migrations WHERE app = '\''guard'\'' AND name = '\''0018_finance_first_page'\''")
    test "$tables" -ge 15
    test "$accounts" -eq 1
    test "$finance" -eq 1
    test "$migration" -eq 1
    su-exec postgres pg_ctl -D /var/lib/postgresql/data -m immediate stop >/dev/null
    echo "RESTORE_DRILL_VERIFIED tables=$tables finance_schema=yes migration_0018=yes network=none"
  '
echo "NOSALE_RESTORE_DRILL_PASS $(date -u +%FT%TZ)"
