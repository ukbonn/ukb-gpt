# Use the lightweight Alpine version
ARG NGINX_LDAP_EGRESS_IMAGE=nginx:alpine
FROM ${NGINX_LDAP_EGRESS_IMAGE}

# 1. Unified Security Installation
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

# Remove the default HTTP config provided by the base image.
# This file contains 'location /' blocks which cause Nginx to crash
# when included inside our 'stream {}' block.
RUN rm /etc/nginx/conf.d/default.conf

# A. Copy the Main Configuration (Skeleton)
COPY images/nginx/ldap_egress/etc/nginx.conf /etc/nginx/nginx.conf

# B. Copy the Dynamic Template (The Logic)
COPY images/nginx/ldap_egress/etc/templates/ldap.conf.template /etc/nginx/templates/ldap.conf.template

# C. Copy the script that dumps the nginx conf in the logs
# We place this in /docker-entrypoint.d/ so it runs AFTER envsubst (via 20-envsubst-on-templates.sh)
# but BEFORE the main nginx process starts. This ensures we verify the FINAL config.
COPY images/nginx/ldap_egress/log_config_at_startup.sh /docker-entrypoint.d/99-log_config_at_startup.sh

# D. Permissions
RUN chown -R nginx:nginx /etc/nginx/conf.d
RUN chmod +x /docker-entrypoint.d/99-log_config_at_startup.sh

# Use the standard Nginx entrypoint (handles env substitution).
# We pass "nginx -g daemon off;" to it via CMD, so it runs the envsubst logic.
ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
