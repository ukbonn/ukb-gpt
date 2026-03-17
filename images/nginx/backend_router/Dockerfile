ARG NGINX_BACKEND_ROUTER_IMAGE=nginx:1.25-alpine
FROM ${NGINX_BACKEND_ROUTER_IMAGE}

# 1. Unified Security Installation
#    --mount=type=bind: Access host files without COPY
#    target=/tmp/security: Where they appear inside
#    rw=false: Read-only for safety
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

# 2. Copy Configs & Generator
# Note: nginx.conf is now a template
COPY images/nginx/backend_router/nginx.conf.template /etc/nginx/nginx.conf.template
COPY images/nginx/backend_router/proxy_params.conf /etc/nginx/proxy_params.conf
COPY images/nginx/backend_router/generate_router_config.sh /usr/local/bin/generate_router_config.sh

RUN chmod +x /usr/local/bin/generate_router_config.sh

# 3. ENTRYPOINT CHAINING
# Added generate_router_config.sh to the chain
ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "/usr/local/bin/generate_router_config.sh", "/docker-entrypoint.sh"]

CMD ["nginx", "-g", "daemon off;"]
