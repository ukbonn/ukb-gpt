# Use the lightweight Alpine version
ARG NGINX_API_EGRESS_IMAGE=nginx:alpine
FROM ${NGINX_API_EGRESS_IMAGE}

# 1. Unified Security Installation
# We reuse the same hardening scripts as the rest of the stack.
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

# 2. Remove the default HTTP config
# The base image ships with /etc/nginx/conf.d/default.conf.
# We remove it so ONLY our templated config is active.
RUN rm /etc/nginx/conf.d/default.conf

# 3. Copy the Main Nginx Skeleton
COPY images/nginx/api_egress/etc/nginx.conf /etc/nginx/nginx.conf

# 4. Copy the Dynamic Template (processed by envsubst)
COPY images/nginx/api_egress/etc/templates/api.conf.template /etc/nginx/templates/api.conf.template

# 5. Log the final config at startup (after envsubst)
COPY images/nginx/api_egress/log_config_at_startup.sh /docker-entrypoint.d/99-log_config_at_startup.sh

# 6. Permissions
RUN chown -R nginx:nginx /etc/nginx/conf.d
RUN chmod +x /docker-entrypoint.d/99-log_config_at_startup.sh

# 7. ENTRYPOINT CHAINING
# Order: [Isolation] -> [Nginx Entrypoint w/ envsubst] -> [Nginx]
ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
