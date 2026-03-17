ARG OPENWEBUI_PIPELINES_IMAGE=ghcr.io/open-webui/pipelines:main
FROM ${OPENWEBUI_PIPELINES_IMAGE}

RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

RUN python3 -m pip install --no-cache-dir holidays

RUN groupadd -r pipelines \
    && useradd -r -g pipelines -d /home/pipelines -m -s /usr/sbin/nologin pipelines \
    && mkdir -p /app/pipelines /opt/ukbgpt/pipelines-src \
    && chown -R pipelines:pipelines /app /home/pipelines /opt/ukbgpt

COPY --chown=pipelines:pipelines images/openwebui/pipelines/ /opt/ukbgpt/pipelines-src/
COPY --chown=pipelines:pipelines images/openwebui/pipelines_entrypoint.sh /usr/local/bin/pipelines_entrypoint.sh
COPY images/openwebui/patch_pipelines_service.py /tmp/patch_pipelines_service.py
RUN chmod +x /usr/local/bin/pipelines_entrypoint.sh
RUN python3 /tmp/patch_pipelines_service.py && rm /tmp/patch_pipelines_service.py

ENV HOST=0.0.0.0 \
    PORT=9099 \
    PIPELINES_DIR=/app/pipelines \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/pipelines \
    XDG_CACHE_HOME=/tmp/.cache

ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "/usr/local/bin/pipelines_entrypoint.sh"]

USER pipelines
