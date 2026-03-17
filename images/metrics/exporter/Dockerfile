# ==============================================================================
# STAGE 1: Binary Extraction
# ==============================================================================
# We use the official image ONLY to get the compiled Go binary.
# We don't run this image directly because it lacks a shell/OS for our security tools.
ARG NGINX_PROMETHEUS_EXPORTER_IMAGE=nginx/nginx-prometheus-exporter:1.3.0
ARG EXPORTER_RUNTIME_IMAGE=alpine:3.19
FROM ${NGINX_PROMETHEUS_EXPORTER_IMAGE} AS binary_source

# ==============================================================================
# STAGE 2: Secure Runtime Construction
# ==============================================================================
FROM ${EXPORTER_RUNTIME_IMAGE}

# 1. Elevate to Root (Default in Alpine, but explicit is good)
USER root

# 2. Unified Security Installation
#    --mount=type=bind: Access host files without COPY
#    target=/tmp/security: Where they appear inside
#    rw=false: Read-only for safety
#    NOTE: This works now because 'alpine' has 'sh' and 'apk'
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

# 3. Copy the Exporter Binary from Stage 1
COPY --from=binary_source /usr/bin/nginx-prometheus-exporter /usr/bin/nginx-prometheus-exporter

# 4. Create a specific non-root user
#    (Alpine doesn't come with a '1001' user by default, we make one)
RUN addgroup -S exporter && adduser -S exporter -G exporter

# 5. Drop Privileges
USER exporter

# 6. Wrap the Entrypoint
#    The security supervisor will start, run checks, and then launch the exporter.
ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "/usr/bin/nginx-prometheus-exporter"]
