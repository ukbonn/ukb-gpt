ARG VLLM_OPENAI_IMAGE=vllm/vllm-openai:v0.14.0
FROM ${VLLM_OPENAI_IMAGE} AS base

ARG EXTRA_PIP_PACKAGES=""

USER root 

# 1. Unified Security Installation
#    --mount=type=bind: Access host files without COPY
#    target=/tmp/security: Where they appear inside
#    rw=false: Read-only for safety
RUN --mount=type=bind,source=security_helpers,target=/tmp/security \
    sh /tmp/security/install_security_helpers.sh

# Optional model-specific runtime dependencies (for example audio stacks).
RUN if [ -n "${EXTRA_PIP_PACKAGES}" ]; then \
        python3 -c 'import shlex,subprocess,sys; subprocess.check_call(["python3","-m","pip","install","--no-cache-dir", *shlex.split(sys.argv[1])])' "${EXTRA_PIP_PACKAGES}"; \
    fi

# We stay as root here because vLLM often requires it for shared memory 
# and GPU driver access depending on the runtime version.
# BUT: Because we do NOT have 'cap_add: NET_ADMIN', this root user 
# cannot touch the network firewall.

FROM base AS stt_adapter

WORKDIR /app
COPY apps/multimodal_stt_adapter/app.py /app/multimodal_stt_adapter.py
COPY apps/common/__init__.py /app/apps/common/__init__.py
COPY apps/common/stt_contract.py /app/apps/common/stt_contract.py

ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "python3", "/app/multimodal_stt_adapter.py"]

FROM base AS api_server

ENTRYPOINT ["/usr/local/bin/active_isolation_monitoring_entrypoint.sh", "python3", "-m", "vllm.entrypoints.openai.api_server"]
