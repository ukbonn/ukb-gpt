FROM python:3.12-slim-bookworm

# Unified security tooling (isolation monitor + health/egress checks).
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

COPY apps/dictation/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY apps/dictation/app.py /app/dictation_app.py
COPY apps/dictation/core.py /app/dictation_core.py
COPY apps/dictation/core.py /app/apps/dictation/core.py
COPY apps/dictation/translate_prompt.txt /app/translate_prompt.txt
COPY apps/dictation/transcription_prompt.txt /app/transcription_prompt.txt
COPY apps/dictation/back_translation_prompt.txt /app/back_translation_prompt.txt

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GRADIO_ANALYTICS_ENABLED=False

ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh"]
CMD ["python", "/app/dictation_app.py"]
