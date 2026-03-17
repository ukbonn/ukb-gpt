#!/bin/sh
set -e

# ------------------------------------------------------------------------------
# Mock API Server Entrypoint (Batch Mode Tests)
# ------------------------------------------------------------------------------
# This script runs inside the mock API container and provides:
#  1) A plain HTTP vLLM-style API on port 8000 (Flask dummy worker)
#  2) A TLS wrapper on port 8443 (socat) to simulate internal HTTPS
# ------------------------------------------------------------------------------

log() {
    echo "[Mock API] $1"
}

log "Validating TLS materials..."
ls -l /server_fullchain.pem /server.key

log "Starting dummy worker (HTTP/8000)..."
python /app/main.py \
    --port=8000 \
    --model=mock-api-model \
    --openwebui-api-compat \
    --disable-v1 &

log "Starting TLS wrapper (HTTPS/8443)..."
# -d -d: verbose diagnostics (printed to container logs)
# reuseaddr: avoid 'address in use' on quick restarts
exec socat -d -d OPENSSL-LISTEN:8443,reuseaddr,cert=/server_fullchain.pem,key=/server.key,verify=0,fork TCP:127.0.0.1:8000
