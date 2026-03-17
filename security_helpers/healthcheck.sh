#!/bin/sh
# ==============================================================================
# PASSIVE LIVENESS CHECK
# ------------------------------------------------------------------------------
# The active security monitoring is handled by secure_entrypoint.sh (PID 1).
# This script verifies the application is answering HTTP requests on localhost.
# ==============================================================================

TIMEOUT_SEC=${TIMEOUT_SEC:-2}

if [ -z "$CHECK_URL" ]; then
    echo "Liveness failed: CHECK_URL must be set." >&2
    exit 1
fi

# Verify Application Liveness via HTTP
if command -v curl >/dev/null 2>&1; then
    # -f (fail on HTTP error), -s (silent), -m (max time)
    if ! curl -f -s -m "$TIMEOUT_SEC" "$CHECK_URL" > /dev/null; then
        echo "Liveness failed: $CHECK_URL unreachable or returned error." >&2
        exit 1
    fi
elif command -v wget >/dev/null 2>&1; then
    # --spider (no download), -q (quiet), -T (timeout)
    if ! wget -q --spider -T "$TIMEOUT_SEC" "$CHECK_URL"; then
        echo "Liveness failed: $CHECK_URL unreachable." >&2
        exit 1
    fi
else
    echo "Liveness failed: neither curl nor wget is available." >&2
    exit 1
fi

exit 0
