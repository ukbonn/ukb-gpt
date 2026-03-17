FROM python:3.12-slim-bookworm

# Unified security tooling (isolation monitor + health/egress checks).
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

COPY apps/dictation/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY apps/dictation/app.py /app/dictation_app.py
COPY apps/dictation/translate_prompt.txt /app/translate_prompt.txt

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GRADIO_ANALYTICS_ENABLED=False

ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh"]
CMD ["python", "/app/dictation_app.py"]
