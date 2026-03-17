ARG DUMMY_VLLM_WORKER_IMAGE=python:3.11-slim
FROM ${DUMMY_VLLM_WORKER_IMAGE}

# 1. Unified Security Installation
#    --mount=type=bind: Access host files without COPY
#    target=/tmp/security: Where they appear inside
#    rw=false: Read-only for safety
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
COPY images/dummy_vllm_worker/main.py .
# Install runtime deps used in tests (socat for TLS wrapping, openssl for cert tooling)
RUN apt-get update \
    && apt-get install -y --no-install-recommends socat openssl \
    && rm -rf /var/lib/apt/lists/*
RUN pip install Flask
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh"]

# Create non-root user and drop privileges
RUN groupadd -r app && useradd -r -g app -d /home/app -m app \
    && chown -R app:app /app /home/app
USER app

