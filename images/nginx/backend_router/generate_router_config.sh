#!/bin/sh
set -e

# Default port if not provided
DEFAULT_PORT="5000"
UPSTREAM_SERVERS=""

echo "[Router-Config] Parsing BACKEND_NODES: $BACKEND_NODES"

# Split comma-separated nodes
IFS=','
for node in $BACKEND_NODES; do
    # Strip whitespace
    node=$(echo "$node" | xargs)
    
    # Check if port is included, if not add default
    case "$node" in
        *:*) UPSTREAM_SERVERS="${UPSTREAM_SERVERS}        server $node max_fails=3 fail_timeout=30s;\n" ;;
        *)   UPSTREAM_SERVERS="${UPSTREAM_SERVERS}        server $node:$DEFAULT_PORT max_fails=3 fail_timeout=30s;\n" ;;
    esac
done

# Export for envsubst
export UPSTREAM_SERVERS

# Generate the final nginx.conf from template
# We only substitute our specific variable to avoid clobbering Nginx variables ($host, etc)
envsubst '${UPSTREAM_SERVERS}' \
    < /etc/nginx/nginx.conf.template \
    > /etc/nginx/nginx.conf

/usr/local/bin/nginx_debug_dump.sh "Router-Config" "" "Configuration generated."
exec "$@"
