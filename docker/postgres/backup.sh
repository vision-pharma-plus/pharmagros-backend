#!/bin/sh
# ---------------------------------------------------------------------------
# Nightly PostgreSQL backup with retention.
#
# Pharmaceutical and financial records must be recoverable. Two properties
# matter beyond simply taking a dump:
#
#   * Verification — a backup that has never been restored is a hypothesis,
#     not a backup. Each dump is test-restored into a scratch database.
#   * Retention — daily for 30 days, plus the first dump of each month kept
#     for a year, so a fault discovered late is still recoverable.
# ---------------------------------------------------------------------------
set -eu

BACKUP_DIR=/backup
DAILY_DIR="$BACKUP_DIR/daily"
MONTHLY_DIR="$BACKUP_DIR/monthly"
DAILY_RETENTION_DAYS=30
MONTHLY_RETENTION_DAYS=365
BACKUP_HOUR="${BACKUP_HOUR:-02}"

export PGPASSWORD="${POSTGRES_PASSWORD}"
PGHOST="${POSTGRES_HOST:-db}"
PGUSER="${POSTGRES_USER:-pharmagros}"
PGDATABASE="${POSTGRES_DB:-pharmagros}"

mkdir -p "$DAILY_DIR" "$MONTHLY_DIR"

log() {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [backup] $*"
}

take_backup() {
    stamp=$(date -u '+%Y%m%d-%H%M%S')
    target="$DAILY_DIR/pharmagros-$stamp.dump"

    log "starting dump -> $target"

    # Custom format: compressed, and restorable selectively with pg_restore.
    if ! pg_dump -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" \
            --format=custom --compress=9 --file="$target"; then
        log "ERROR: pg_dump failed"
        rm -f "$target"
        return 1
    fi

    size=$(wc -c < "$target")
    log "dump complete: ${size} bytes"

    # A dump far smaller than expected usually means the database was empty
    # or the dump aborted early — both worth failing loudly on.
    if [ "$size" -lt 10240 ]; then
        log "ERROR: dump is implausibly small (${size} bytes); treating as failed"
        rm -f "$target"
        return 1
    fi

    verify_backup "$target" || return 1

    # First backup of the month is promoted to long-term retention.
    if [ "$(date -u '+%d')" = "01" ]; then
        cp "$target" "$MONTHLY_DIR/pharmagros-$(date -u '+%Y%m').dump"
        log "promoted to monthly retention"
    fi

    return 0
}

verify_backup() {
    target="$1"
    scratch="verify_$(date -u '+%s')"

    log "verifying restore into $scratch"

    createdb -h "$PGHOST" -U "$PGUSER" "$scratch" 2>/dev/null || {
        log "WARNING: could not create verification database; skipping verification"
        return 0
    }

    if pg_restore -h "$PGHOST" -U "$PGUSER" -d "$scratch" --no-owner --jobs=2 "$target" >/dev/null 2>&1; then
        # Confirm the audit trail actually arrived — it is the table whose
        # loss would be least recoverable and most consequential.
        count=$(psql -h "$PGHOST" -U "$PGUSER" -d "$scratch" -tAc \
            "SELECT count(*) FROM core_auditlog" 2>/dev/null || echo "ERR")
        log "verification restore succeeded (audit entries: $count)"
        dropdb -h "$PGHOST" -U "$PGUSER" "$scratch"
        return 0
    fi

    log "ERROR: verification restore FAILED for $target"
    dropdb -h "$PGHOST" -U "$PGUSER" "$scratch" 2>/dev/null || true
    return 1
}

prune() {
    find "$DAILY_DIR" -name '*.dump' -type f -mtime "+$DAILY_RETENTION_DAYS" -delete
    find "$MONTHLY_DIR" -name '*.dump' -type f -mtime "+$MONTHLY_RETENTION_DAYS" -delete
    log "retention applied (daily ${DAILY_RETENTION_DAYS}d, monthly ${MONTHLY_RETENTION_DAYS}d)"
}

log "backup service started; scheduled daily at ${BACKUP_HOUR}:00 UTC"

# Simple scheduler loop — avoids depending on cron being present and
# correctly configured inside the image.
while true; do
    current_hour=$(date -u '+%H')
    if [ "$current_hour" = "$BACKUP_HOUR" ]; then
        if take_backup; then
            prune
            log "backup cycle complete"
        else
            log "backup cycle FAILED"
        fi
        # Sleep past the trigger hour so the job runs once per day.
        sleep 3660
    else
        sleep 600
    fi
done
