#!/bin/bash
set -e

# --- 1. Dynamic Root CA Installation ---
CUSTOM_CA_PATH="/usr/local/share/ca-certificates/corporate_root.crt"

# Check if the file exists and has size greater than 0 (avoids /dev/null issues)
if [ -s "$CUSTOM_CA_PATH" ]; then
    echo "[certificate installation] Detected custom Root CA at $CUSTOM_CA_PATH"
    if [ -w /etc/ssl/certs ]; then
        echo "[certificate installation] Updating certificate store..."
        chmod 644 "$CUSTOM_CA_PATH" || true
        update-ca-certificates || true
    else
        echo "[certificate installation] Read-only rootfs detected. Using custom CA via SSL_CERT_FILE."
        BUNDLE_PATH="/app/backend/data/ca_bundle.crt"
        if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
            cat /etc/ssl/certs/ca-certificates.crt "$CUSTOM_CA_PATH" > "$BUNDLE_PATH"
        else
            cat "$CUSTOM_CA_PATH" > "$BUNDLE_PATH"
        fi
        export SSL_CERT_FILE="$BUNDLE_PATH"
        export REQUESTS_CA_BUNDLE="$BUNDLE_PATH"
    fi
else
    echo "[certificate installation] No custom Root CA mounted. Skipping."
fi

# --- 2. CHAIN EXECUTION ---
# Continue entrypoint chain after optional CA setup.
exec "$@"
