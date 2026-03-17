#!/bin/sh
set -eu

log() {
    echo "[chat-purger] $1"
}

RETENTION_DAYS="${CHAT_HISTORY_RETENTION_DAYS:-180}"
INTERVAL="${CHAT_HISTORY_PURGE_INTERVAL_SECONDS:-86400}"

log "Starting chat purge loop (retention=${RETENTION_DAYS} days, interval=${INTERVAL}s)."

while true; do
    CHAT_HISTORY_RETENTION_DAYS="$RETENTION_DAYS" /usr/local/bin/chat_retention.py
    sleep "$INTERVAL"
done
