ARG NGINX_INGRESS_IMAGE=nginx:1.25-alpine
FROM ${NGINX_INGRESS_IMAGE}

# 1. Unified Security Installation
#    --mount=type=bind: Access host files without COPY
#    target=/tmp/security: Where they appear inside
#    rw=false: Read-only for safety
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

# 2. Copy Configs & Templates
COPY images/nginx/ingress/etc/nginx.conf /etc/nginx/nginx.conf
# We copy templates to a staging area, NOT the default /etc/nginx/templates/
# because we want to manually process them in our script.
COPY images/nginx/ingress/etc/templates /etc/nginx/templates_staging/

# 3. Copy Secret Shim & Configurer
COPY images/nginx/ingress/secret_shim.sh /usr/local/bin/secret_shim.sh
COPY images/nginx/ingress/generate_ingress_config.sh /usr/local/bin/generate_ingress_config.sh
RUN chmod +x /usr/local/bin/secret_shim.sh /usr/local/bin/generate_ingress_config.sh

# 5. Prepare writable paths for non-root + read-only rootfs
RUN mkdir -p /etc/nginx/conf.d /var/cache/nginx /var/log/nginx /var/run/nginx /run/secrets \
    && chown -R nginx:nginx /etc/nginx/conf.d /var/cache/nginx /var/log/nginx /var/run/nginx /run/secrets

# 4. ENTRYPOINT CHAINING
# Order: [Isolation] -> [Secret Shim] -> [Config Generator] -> [Nginx]
ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "/usr/local/bin/secret_shim.sh", "/usr/local/bin/generate_ingress_config.sh"]

CMD ["nginx", "-g", "daemon off;"]

# Run as non-root
USER nginx
