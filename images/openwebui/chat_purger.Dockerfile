ARG CHAT_PURGER_IMAGE=python:3.12-slim-bookworm
FROM ${CHAT_PURGER_IMAGE}

# 1. Unified Security Installation
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

# 2. Purger logic
COPY images/openwebui/chat_retention.py /usr/local/bin/chat_retention.py
COPY images/openwebui/chat_purger_loop.sh /usr/local/bin/chat_purger_loop.sh
RUN chmod 755 /usr/local/bin/chat_retention.py /usr/local/bin/chat_purger_loop.sh

# 3. Non-root runtime identity
RUN groupadd -r purger && useradd -r -g purger -d /home/purger -s /bin/sh purger \
    && mkdir -p /home/purger \
    && chown -R purger:purger /home/purger

ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "sh", "/usr/local/bin/chat_purger_loop.sh"]
USER purger
