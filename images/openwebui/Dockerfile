ARG OPENWEBUI_IMAGE=ghcr.io/open-webui/open-webui:v0.8.3
FROM ${OPENWEBUI_IMAGE}

ARG EXTRA_PIP_PACKAGES=""

# 1. Unified Security Installation
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

RUN if [ -n "${EXTRA_PIP_PACKAGES}" ]; then \
        python3 -c 'import shlex,subprocess,sys; subprocess.check_call(["python3","-m","pip","install","--no-cache-dir", *shlex.split(sys.argv[1])])' "${EXTRA_PIP_PACKAGES}"; \
    fi

# 2. Copy cert install script TO ROOT (/) explicitly
COPY images/openwebui/certificate_installation.sh /certificate_installation.sh
RUN chmod +x /certificate_installation.sh

# 2.1 Create non-root user and ensure writable data dir
RUN groupadd -r webui && useradd -r -g webui -d /app -s /bin/bash webui \
    && mkdir -p /app/backend/data /app/backend/data/.cache \
    && chown -R webui:webui /app /app/backend/data

COPY images/openwebui/patch_pipelines_router.py /tmp/patch_pipelines_router.py
RUN python3 /tmp/patch_pipelines_router.py && rm /tmp/patch_pipelines_router.py

# 3. Entrypoint Chaining
#    This looks for the file at the root, which now matches the COPY command.
ENTRYPOINT ["/certificate_installation.sh", "/usr/local/bin/active_isolation_monitoring_entrypoint.sh"]
CMD ["bash", "start.sh"]

# Run as non-root by default
USER webui
