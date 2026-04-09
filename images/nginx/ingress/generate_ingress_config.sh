#!/bin/sh
set -e

# ------------------------------------------------------------------------------
# MODE FLAG (DEFAULT)
# ------------------------------------------------------------------------------
# We explicitly normalize the flag so this script can branch safely.
BATCH_CLIENT_MODE_ON="${BATCH_CLIENT_MODE_ON:-false}"
ENABLE_INTERNAL_METRICS="${ENABLE_INTERNAL_METRICS:-false}"
ENABLE_DICTATION_APP="${ENABLE_DICTATION_APP:-false}"
ENABLE_ICD_10_CODING_APP="${ENABLE_ICD_10_CODING_APP:-false}"
ENABLE_COHORT_FEASIBILITY_APP="${ENABLE_COHORT_FEASIBILITY_APP:-false}"
ENABLE_METRICS_FORWARDING="${ENABLE_METRICS_FORWARDING:-false}"
BYPASS_ROUTER="${BYPASS_ROUTER:-true}"
LLM_BYPASS_ROUTER="${LLM_BYPASS_ROUTER:-$BYPASS_ROUTER}"
EMBEDDING_BYPASS_ROUTER="${EMBEDDING_BYPASS_ROUTER:-true}"
LLM_BACKEND_NODES="${LLM_BACKEND_NODES:-${BACKEND_NODES:-}}"
EMBEDDING_BACKEND_NODES="${EMBEDDING_BACKEND_NODES:-}"
BATCH_CLIENT_DIRECT_PORT_START="${BATCH_CLIENT_DIRECT_PORT_START:-30001}"
BATCH_CLIENT_DIRECT_PORT_END="${BATCH_CLIENT_DIRECT_PORT_END:-30032}"

echo "[Ingress Configurer] Startup policy: immediate ingress start (no upstream wait)."

echo "[Ingress Configurer] Generating Nginx Configuration..."

# Shared config used by both web and batch templates:
# - websocket upgrade map
# - internal :8080 status server
cp /etc/nginx/templates_staging/common.conf /etc/nginx/conf.d/00-common.conf

# ------------------------------------------------------------------------------
# 1. GRAFANA LOGIC
# ------------------------------------------------------------------------------
if [ "$ENABLE_INTERNAL_METRICS" = "true" ]; then
    echo "[Ingress Configurer] Mode: Internal Metrics (Grafana) ENABLED."

    # Use shell variable for the location block injected into web/batch template.
    export GRAFANA_LOCATION_BLOCK='
    location /grafana/ {
        proxy_pass http://grafana:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }'
else
    echo "[Ingress Configurer] Mode: Internal Metrics DISABLED."
    export GRAFANA_LOCATION_BLOCK=""
fi

if [ "$ENABLE_DICTATION_APP" = "true" ] && [ "$BATCH_CLIENT_MODE_ON" != "true" ]; then
    echo "[Ingress Configurer] Mode: Dictation App Route ENABLED (/dictation/)."
    export DICTATION_LOCATION_BLOCK='
    location = /dictation {
        return 301 /dictation/;
    }

    location /dictation/ {
        proxy_pass http://dictation:7860/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }'
else
    echo "[Ingress Configurer] Mode: Dictation App Route DISABLED."
    export DICTATION_LOCATION_BLOCK=""
fi

if [ "$ENABLE_ICD_10_CODING_APP" = "true" ] && [ "$BATCH_CLIENT_MODE_ON" != "true" ]; then
    echo "[Ingress Configurer] Mode: ICD-10 Coding Route ENABLED (/api/v1/icd10/)."
    export ICD10_LOCATION_BLOCK='
    location = /api/v1/icd10 {
        return 307 /api/v1/icd10/status;
    }

    location /api/v1/icd10/ {
        proxy_pass http://icd_10_coding:8091;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }'
else
    echo "[Ingress Configurer] Mode: ICD-10 Coding Route DISABLED."
    export ICD10_LOCATION_BLOCK=""
fi

if [ "$ENABLE_COHORT_FEASIBILITY_APP" = "true" ] && [ "$BATCH_CLIENT_MODE_ON" = "true" ]; then
    echo "[Ingress Configurer] Mode: Cohort Feasibility Route ENABLED (/feasibility/)."
    export COHORT_FEASIBILITY_LOCATION_BLOCK='
    location = /feasibility {
        return 301 /feasibility/;
    }

    location /feasibility/ {
        proxy_pass http://cohort_feasibility:8090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }'
else
    echo "[Ingress Configurer] Mode: Cohort Feasibility Route DISABLED."
    export COHORT_FEASIBILITY_LOCATION_BLOCK=""
fi

if [ "$ENABLE_METRICS_FORWARDING" = "true" ]; then
    echo "[Ingress Configurer] Mode: Backend Metrics Port Forwarding ENABLED."
else
    echo "[Ingress Configurer] Mode: Backend Metrics Port Forwarding DISABLED."
fi

# ------------------------------------------------------------------------------
# 2. MAIN WEB CONFIG (STANDARD vs BATCH)
# ------------------------------------------------------------------------------

# Determine which template we should render
NGINX_TEMPLATE="/etc/nginx/templates_staging/web.conf.template"

if [ "$BATCH_CLIENT_MODE_ON" = "true" ]; then
    echo "[Ingress Configurer] Mode: Batch Client (HTTP/Localhost)."
    NGINX_TEMPLATE="/etc/nginx/templates_staging/batch.conf.template"

    # Build chat and embedding upstream includes from service discovery env vars.
    BATCH_CHAT_UPSTREAMS_FILE="/etc/nginx/conf.d/batch_chat_upstreams.inc"
    BATCH_EMBEDDING_UPSTREAMS_FILE="/etc/nginx/conf.d/batch_embedding_upstreams.inc"
    BATCH_DIRECT_SERVERS_FILE="/etc/nginx/conf.d/batch_direct_servers.conf"
    BATCH_RUNTIME_INFO_FILE="/run/ukbgpt_runtime.json"
    : > "$BATCH_CHAT_UPSTREAMS_FILE"
    : > "$BATCH_EMBEDDING_UPSTREAMS_FILE"
    : > "$BATCH_DIRECT_SERVERS_FILE"

    append_nodes() {
        target_file="$1"
        nodes="$2"
        IFS_BAK="$IFS"
        IFS=','
        for node in $nodes; do
            node="$(echo "$node" | xargs)"
            if [ -n "$node" ]; then
                echo "    server ${node} max_conns=512;" >> "$target_file"
            fi
        done
        IFS="$IFS_BAK"
    }

    json_array_from_csv() {
        nodes="$1"
        out=""
        sep=""
        IFS_BAK="$IFS"
        IFS=','
        for node in $nodes; do
            node="$(echo "$node" | xargs)"
            if [ -n "$node" ]; then
                escaped="$(printf '%s' "$node" | sed 's/\\/\\\\/g; s/"/\\"/g')"
                out="${out}${sep}\"${escaped}\""
                sep=", "
            fi
        done
        IFS="$IFS_BAK"
        printf '[%s]' "$out"
    }

    if [ "$LLM_BYPASS_ROUTER" = "false" ]; then
        echo "    server backend_router:5000 max_conns=512;" >> "$BATCH_CHAT_UPSTREAMS_FILE"
    else
        append_nodes "$BATCH_CHAT_UPSTREAMS_FILE" "${LLM_BACKEND_NODES:-}"
    fi

    # Backward compatibility for older startup env exports.
    if [ ! -s "$BATCH_CHAT_UPSTREAMS_FILE" ]; then
        if [ "$BYPASS_ROUTER" = "false" ]; then
            echo "    server backend_router:5000 max_conns=512;" >> "$BATCH_CHAT_UPSTREAMS_FILE"
        else
            append_nodes "$BATCH_CHAT_UPSTREAMS_FILE" "${BACKEND_NODES:-}"
        fi
    fi

    if [ "$EMBEDDING_BYPASS_ROUTER" = "false" ]; then
        echo "    server embedding_backend_router:5000 max_conns=512;" >> "$BATCH_EMBEDDING_UPSTREAMS_FILE"
    else
        append_nodes "$BATCH_EMBEDDING_UPSTREAMS_FILE" "${EMBEDDING_BACKEND_NODES:-}"
    fi
    embedding_has_local_upstreams="false"
    if [ -s "$BATCH_EMBEDDING_UPSTREAMS_FILE" ]; then
        embedding_has_local_upstreams="true"
    fi

    additional_llm_api_configured="false"
    additional_embedding_api_configured="false"
    egress_port="${BATCH_CLIENT_EGRESS_PORT:-30100}"
    if [ "${BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ENABLED:-false}" = "true" ]; then
        additional_llm_api_configured="true"
        echo "    server api_egress:${egress_port} max_conns=256;" >> "$BATCH_CHAT_UPSTREAMS_FILE"
    fi
    if [ "${BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ENABLED:-false}" = "true" ]; then
        additional_embedding_api_configured="true"
        echo "    server api_egress:${egress_port} max_conns=256;" >> "$BATCH_EMBEDDING_UPSTREAMS_FILE"
    fi

    # If no dedicated embedding backend exists, reuse the local chat upstreams
    # and keep any embedding-specific api_egress entry that may have been added.
    if [ "$embedding_has_local_upstreams" != "true" ] && [ -s "$BATCH_CHAT_UPSTREAMS_FILE" ]; then
        tmp_embedding_upstreams="$(mktemp)"
        grep -v "server api_egress:${egress_port} " "$BATCH_CHAT_UPSTREAMS_FILE" > "$tmp_embedding_upstreams" || true
        if [ -s "$BATCH_EMBEDDING_UPSTREAMS_FILE" ]; then
            cat "$BATCH_EMBEDDING_UPSTREAMS_FILE" >> "$tmp_embedding_upstreams"
        fi
        mv "$tmp_embedding_upstreams" "$BATCH_EMBEDDING_UPSTREAMS_FILE"
    fi

    # If chat backend is intentionally disabled (embedding-only), reuse embedding upstreams.
    if [ ! -s "$BATCH_CHAT_UPSTREAMS_FILE" ] && [ -s "$BATCH_EMBEDDING_UPSTREAMS_FILE" ]; then
        cat "$BATCH_EMBEDDING_UPSTREAMS_FILE" > "$BATCH_CHAT_UPSTREAMS_FILE"
    fi

    if [ ! -s "$BATCH_CHAT_UPSTREAMS_FILE" ] || [ ! -s "$BATCH_EMBEDDING_UPSTREAMS_FILE" ]; then
        echo "[Ingress Configurer] ❌ CRITICAL: batch upstream list is empty."
        echo "[Ingress Configurer]     Ensure LLM/embedding discovery variables are set."
        exit 1
    fi

    # --------------------------------------------------------------
    # Direct worker listeners (localhost-only host bindings)
    # --------------------------------------------------------------
    llm_direct_endpoints_json=""
    llm_direct_sep=""
    direct_ports_enabled="true"
    additional_api_direct_base_url="null"
    additional_api_direct_port="null"

    case "$BATCH_CLIENT_DIRECT_PORT_START" in
        ''|*[!0-9]*)
            echo "[Ingress Configurer] ❌ BATCH_CLIENT_DIRECT_PORT_START must be numeric."
            exit 1
            ;;
    esac
    case "$BATCH_CLIENT_DIRECT_PORT_END" in
        ''|*[!0-9]*)
            echo "[Ingress Configurer] ❌ BATCH_CLIENT_DIRECT_PORT_END must be numeric."
            exit 1
            ;;
    esac
    if [ "$BATCH_CLIENT_DIRECT_PORT_START" -gt "$BATCH_CLIENT_DIRECT_PORT_END" ]; then
        echo "[Ingress Configurer] ❌ BATCH_CLIENT_DIRECT_PORT_START must be <= BATCH_CLIENT_DIRECT_PORT_END."
        exit 1
    fi
    if [ "$BATCH_CLIENT_DIRECT_PORT_START" -le "$BATCH_CLIENT_LISTEN_PORT" ] && [ "$BATCH_CLIENT_LISTEN_PORT" -le "$BATCH_CLIENT_DIRECT_PORT_END" ]; then
        echo "[Ingress Configurer] ❌ Direct worker port range overlaps router listen port ${BATCH_CLIENT_LISTEN_PORT}."
        exit 1
    fi

    next_direct_port="$BATCH_CLIENT_DIRECT_PORT_START"
    llm_nodes_for_direct="${LLM_BACKEND_NODES:-${BACKEND_NODES:-}}"
    IFS_BAK="$IFS"
    IFS=','
    for node in $llm_nodes_for_direct; do
        node="$(echo "$node" | xargs)"
        if [ -z "$node" ]; then
            continue
        fi
        if [ "$next_direct_port" -gt "$BATCH_CLIENT_DIRECT_PORT_END" ]; then
            echo "[Ingress Configurer] ⚠️ Direct worker port range exhausted at ${BATCH_CLIENT_DIRECT_PORT_END}."
            break
        fi

        cat >> "$BATCH_DIRECT_SERVERS_FILE" <<EOF
server {
    listen ${next_direct_port};
    server_name ${SERVER_NAME} ingress localhost 127.0.0.1;
    client_max_body_size 100M;
    ${NGINX_ACL_ALLOW_LIST}
    deny all;
    limit_req zone=perip_limit burst=1000 nodelay;

    location / {
        proxy_pass http://${node};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location /health {
        access_log off;
        return 200 "healthy\\n";
    }
}

EOF

        llm_direct_endpoints_json="${llm_direct_endpoints_json}${llm_direct_sep}{\"upstream\":\"${node}\",\"base_url\":\"http://127.0.0.1:${next_direct_port}/v1\"}"
        llm_direct_sep=", "
        next_direct_port=$((next_direct_port + 1))
    done
    IFS="$IFS_BAK"

    if [ "$additional_llm_api_configured" = "true" ] && [ "$next_direct_port" -le "$BATCH_CLIENT_DIRECT_PORT_END" ]; then
        cat >> "$BATCH_DIRECT_SERVERS_FILE" <<EOF
server {
    listen ${next_direct_port};
    server_name ${SERVER_NAME} ingress localhost 127.0.0.1;
    client_max_body_size 100M;
    ${NGINX_ACL_ALLOW_LIST}
    deny all;
    limit_req zone=perip_limit burst=1000 nodelay;

    location / {
        proxy_pass http://api_egress:${egress_port};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_buffering off;
        proxy_connect_timeout 60s;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location /health {
        access_log off;
        return 200 "healthy\\n";
    }
}

EOF
        additional_api_direct_base_url="\"http://127.0.0.1:${next_direct_port}/v1\""
        additional_api_direct_port="${next_direct_port}"
        next_direct_port=$((next_direct_port + 1))
    fi

    llm_upstreams_json="$(json_array_from_csv "${LLM_BACKEND_NODES:-${BACKEND_NODES:-}}")"
    embedding_upstreams_json="$(json_array_from_csv "${EMBEDDING_BACKEND_NODES:-}")"
    cat > "$BATCH_RUNTIME_INFO_FILE" <<EOF
{
  "schema_version": "2026-02-20",
  "batch_mode": true,
  "router": {
    "base_url": "http://127.0.0.1:${BATCH_CLIENT_LISTEN_PORT}/v1"
  },
  "direct_worker_ports_enabled": ${direct_ports_enabled},
  "direct_worker_port_start": ${BATCH_CLIENT_DIRECT_PORT_START},
  "direct_worker_port_end": ${BATCH_CLIENT_DIRECT_PORT_END},
  "llm_upstreams": ${llm_upstreams_json},
  "embedding_upstreams": ${embedding_upstreams_json},
  "llm_direct_endpoints": [${llm_direct_endpoints_json}],
  "additional_api": {
    "configured": ${additional_llm_api_configured},
    "direct_port": ${additional_api_direct_port},
    "direct_base_url": ${additional_api_direct_base_url}
  },
  "additional_embedding_api": {
    "configured": ${additional_embedding_api_configured}
  }
}
EOF
else
    echo "[Ingress Configurer] Mode: Standard WebUI (HTTPS)."
fi

# Build localhost-only scrape tunnels directly from BACKEND_NODES.
# These listeners intentionally use plain HTTP; TLS termination stays on the main
# WebUI/Grafana ingress at :443, while diagnostics remain loopback-bound.
METRICS_TUNNEL_FILE="/etc/nginx/conf.d/metrics_tunnel.conf"
: > "$METRICS_TUNNEL_FILE"

if [ "$ENABLE_METRICS_FORWARDING" = "true" ] && [ -n "${BACKEND_NODES:-}" ]; then
    i=0
    IFS_BAK="$IFS"
    IFS=','
    for node in $BACKEND_NODES; do
        node="$(echo "$node" | xargs)"
        if [ -z "$node" ]; then
            continue
        fi
        listen_port=$((5000 + i))
        cat >> "$METRICS_TUNNEL_FILE" <<EOF
server {
    listen ${listen_port};
    server_name _;
    access_log off;
    location / {
        return 403 'Security Alert: Diagnostic tunnel restricted to /metrics.\\n';
    }
    location /metrics {
        proxy_pass http://${node}/metrics;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF
        i=$((i + 1))
    done
    IFS="$IFS_BAK"
fi

if [ "$ENABLE_INTERNAL_METRICS" = "true" ]; then
    cat >> "$METRICS_TUNNEL_FILE" <<'EOF'
server {
    listen 8001;
    server_name _;
    access_log off;

    location / {
        proxy_pass http://exporter:9113;
        proxy_set_header Host $host;
    }
}
EOF
fi

if [ -s "$METRICS_TUNNEL_FILE" ]; then
    echo "[Ingress Configurer] Rendering metrics tunnel config..."
else
    echo "[Ingress Configurer] Metrics tunnel config not required."
    rm -f "$METRICS_TUNNEL_FILE"
fi

VARS_FOR_NGINX='$SERVER_NAME$NGINX_ACL_ALLOW_LIST$GRAFANA_LOCATION_BLOCK$DICTATION_LOCATION_BLOCK$ICD10_LOCATION_BLOCK$COHORT_FEASIBILITY_LOCATION_BLOCK$BATCH_CLIENT_LISTEN_PORT'

# Substitute all remaining variables into the chosen template
envsubst "$VARS_FOR_NGINX" \
    < "$NGINX_TEMPLATE" \
    > /etc/nginx/conf.d/default.conf

/usr/local/bin/nginx_debug_dump.sh "Ingress Configurer" "" "Configuration complete."
exec "$@"
