ARG GRAFANA_IMAGE=grafana/grafana:10.0.0
FROM ${GRAFANA_IMAGE}

USER root

# 1. Unified Security Installation
#    --mount=type=bind: Access host files without COPY
#    target=/tmp/security: Where they appear inside
#    rw=false: Read-only for safety
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

# 2. Provisioning Configs
COPY images/metrics/grafana/provisioning /etc/grafana/provisioning

# 3. Switch back to Grafana User
USER 472

# 4. Wrap the Entrypoint
#    Grafana's original entrypoint is /run.sh
ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "/run.sh"]
