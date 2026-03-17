#!/bin/sh
set -e

LABEL="${1:-Nginx-Config}"
EXTRA_CONFIG_PATH="${2:-}"
QUIET_MESSAGE="${3:-}"

if [ "${DEBUG_NGINX_CONFIG_DUMP:-false}" = "true" ]; then
    echo "[${LABEL}] Dumping final nginx configuration:"
    nginx -T
    if [ -n "${EXTRA_CONFIG_PATH}" ] && [ -f "${EXTRA_CONFIG_PATH}" ]; then
        echo "[${LABEL}] Contents of ${EXTRA_CONFIG_PATH}:"
        cat "${EXTRA_CONFIG_PATH}"
    fi
elif [ -n "${QUIET_MESSAGE}" ]; then
    echo "[${LABEL}] ${QUIET_MESSAGE}"
fi
