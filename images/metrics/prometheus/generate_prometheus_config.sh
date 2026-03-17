#!/bin/sh
set -e

echo "[Prometheus-Config] Generating scrape targets..."

# Construct the list of worker targets for the YAML config
# Input format from start.py: "worker_0:5000,worker_1:5000"
# Output format for Prometheus: "'worker_0:5000', 'worker_1:5000'"
PROMETHEUS_WORKER_TARGETS=$(echo "$BACKEND_NODES" | sed "s/,/', '/g" | sed "s/^/'/" | sed "s/$/'/")

export PROMETHEUS_WORKER_TARGETS

# Inject into template
envsubst '${PROMETHEUS_WORKER_TARGETS}' \
    < /usr/share/prometheus/prometheus.yml.template \
    > /etc/prometheus/prometheus.yml

echo "[Prometheus-Config] Targets: $PROMETHEUS_WORKER_TARGETS"
exec "$@"
