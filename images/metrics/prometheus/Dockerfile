# ==============================================================================
# STAGE 1: Binary Extraction
# ==============================================================================
# We use the official image to get the specific version of the binary and assets.
ARG PROMETHEUS_IMAGE=prom/prometheus:v2.45.0
ARG PROMETHEUS_RUNTIME_IMAGE=alpine:3.19
FROM ${PROMETHEUS_IMAGE} AS binary_source

# ==============================================================================
# STAGE 2: Secure Runtime Construction
# ==============================================================================
# We switch to Alpine to ensure we have a shell and package manager (apk)
# compatible with our 'install_security_helpers.sh' script.
FROM ${PROMETHEUS_RUNTIME_IMAGE}

# 1. Elevate to Root (Default in Alpine, but explicit is good)
USER root

# 2. Unified Security Installation
#    --mount=type=bind: Access host files without COPY
#    target=/tmp/security: Where they appear inside
#    rw=false: Read-only for safety
#    FIX: Added 'gettext' to install 'envsubst', which is required by generate_prometheus_config.sh
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    apk add --no-cache gettext && \
    sh /tmp/security/install_security_helpers.sh

# 3. Setup Directories & Permissions
#    Prometheus needs a data directory and a config directory.
#    We use 'nobody' (GID 65534) which exists by default in Alpine.
RUN mkdir -p /etc/prometheus /prometheus /usr/share/prometheus && \
    chown -R nobody:nobody /etc/prometheus /prometheus /usr/share/prometheus

# 4. Copy Binaries & Assets from Stage 1
COPY --from=binary_source /bin/prometheus /bin/prometheus
COPY --from=binary_source /bin/promtool /bin/promtool
COPY --from=binary_source /usr/share/prometheus/console_libraries /usr/share/prometheus/console_libraries
COPY --from=binary_source /usr/share/prometheus/consoles /usr/share/prometheus/consoles

# 5. Copy Our Custom Config Logic
# Keep the template in a read-only location; runtime will write final config into /etc/prometheus.
COPY images/metrics/prometheus/prometheus.yml.template /usr/share/prometheus/prometheus.yml.template
COPY images/metrics/prometheus/generate_prometheus_config.sh /usr/local/bin/generate_prometheus_config.sh
RUN chmod +x /usr/local/bin/generate_prometheus_config.sh && \
    chown nobody:nobody /usr/share/prometheus/prometheus.yml.template

# 6. Drop Privileges
USER nobody
WORKDIR /prometheus

# 7. Wrap the Entrypoint
#    Entrypoint: [Isolation Monitor] -> [Config Generator] -> [Prometheus]
ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "/usr/local/bin/generate_prometheus_config.sh", "/bin/prometheus"]

# 8. Default Arguments
CMD ["--config.file=/etc/prometheus/prometheus.yml", \
     "--storage.tsdb.path=/prometheus", \
     "--web.console.libraries=/usr/share/prometheus/console_libraries", \
     "--web.console.templates=/usr/share/prometheus/consoles"]
