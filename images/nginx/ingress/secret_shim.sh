#!/bin/sh
set -e

# ==============================================================================
# NGINX SECRET SHIM
# ==============================================================================
# The Nginx configuration expects the private key to exist as a file at
# /run/secrets/certificate_key.
#
# WHY NOT NATIVE DOCKER SECRETS?
# Although modern Docker Compose (v2.23+) supports 'secrets' from env vars, 
# this shim is explicitly used for:
# 1. PORTABILITY: Ensures compatibility with older Docker versions (2.x) often 
#    found in restricted/air-gapped clinical environments.
# 2. FAIL-FAST VALIDATION: Provides immediate, human-readable error logs if 
#    secrets are missing, whereas native Docker mounts can fail silently or cryptically.
# 3. GRANULAR CONTROL: Allows explicit 'chmod 600' enforcement on the resulting 
#    RAM-file to satisfy strict security audits.
#
# Since we inject secrets via Environment Variables (RAM) to avoid disk persistence,
# this script writes the env var to a tmpfs file before Nginx starts.
# ==============================================================================

echo "[Ingress-Secret-Shim] Initializing secrets..."

# ------------------------------------------------------------------------------
# BATCH CLIENT MODE SHORT-CIRCUIT
# ------------------------------------------------------------------------------
# In batch mode we run HTTP only on localhost and do NOT require TLS keys.
# We therefore skip secret injection entirely.
if [ "$BATCH_CLIENT_MODE_ON" = "true" ]; then
    echo "[Ingress-Secret-Shim] Batch Client Mode detected. Skipping TLS secret injection."
    exec "$@"
fi

# 1. Create the secrets directory (should be tmpfs in memory)
mkdir -p /run/secrets

# 2. Inject the Private Key
if [ -n "$CERTIFICATE_KEY" ]; then
    echo "$CERTIFICATE_KEY" > /run/secrets/certificate_key
    
    # Secure permissions: Read/Write for owner only
    chmod 600 /run/secrets/certificate_key
    
    echo "[Ingress-Secret-Shim] SSL Key injected successfully."
else
    echo "[Ingress-Secret-Shim] CRITICAL ERROR: CERTIFICATE_KEY environment variable is empty."
    echo "[Ingress-Secret-Shim] Nginx cannot start without the SSL private key."
    exit 1
fi

# 3. Hand over control to the next command (Nginx)
exec "$@"
