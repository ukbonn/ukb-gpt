# Common Setup

This page covers the preparation shared by both runtime modes.

## Host Prerequisites

Required for all deployments:

1. NVIDIA GPUs with enough VRAM for the selected model family and deployment layout.
2. Linux host with Docker Engine and Docker Compose V2.
3. NVIDIA Container Toolkit or CDI configured so this succeeds:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

4. Offline model artifacts already available on the host.
5. Python 3 available to run `start.py`.
6. Host Python dependencies installed from `requirements.txt`.

Required only for chatbot provider mode:

1. A valid PEM TLS certificate chain and matching private key for ingress.

## Repository Bootstrap

```bash
git clone <your-repo-url>.git
cd <repo-dir>

python3 -m venv .venv
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -r requirements.txt
```

## Testing-Only Localhost TLS Shortcut

For local testing only:

```bash
python3 utils/scripts/create_localhost_pki.py --server-name localhost
source ~/.ukbgpt-localhost-pki/env.localhost.sh
export WEBUI_SECRET_KEY="$(openssl rand -hex 32)"
```

Important mapping from the generated files:

- `SSL_CERT_PATH` must point to `fullchain.pem`
- `ROOT_CA_PATH` must point to `root_ca.crt`
- `CERTIFICATE_KEY` is populated from `server.key`

## Choose Model Backends

Backend selection is deployment-config driven:

- `MODEL_DEPLOYMENT_CONFIG`
- `EMBEDDING_MODEL_DEPLOYMENT_CONFIG`
- `STT_MODEL_DEPLOYMENT_CONFIG`

Each variable accepts either:

- a direct absolute path
- a repo-relative path

Example:

```bash
export MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/gpt-oss-120b.2x2.toml"
export EMBEDDING_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/gte-qwen2.single-gpu.toml"
export STT_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-mini-4b.single-gpu.toml"
```

Notes:

- leave `MODEL_DEPLOYMENT_CONFIG` empty for embedding-only deployments
- STT is normally additive; STT-only backend mode is intended for the dictation app
- model family metadata and deployment examples are documented in [compose/models/README.md](../compose/models/README.md)

## Worker Image Variables

Worker image resolution follows this order:

- `VLLM_OPENAI_IMAGE_LLM`
- `VLLM_OPENAI_IMAGE_EMBEDDING`
- `VLLM_OPENAI_IMAGE_STT`
- `VLLM_OPENAI_IMAGE`

Worker-image defaults are declared per selected model family in `compose/models/**/model.toml`.
Resolution order is role-specific override, then `VLLM_OPENAI_IMAGE`, then the selected family default.

## `gpt-oss` Encodings

If you select a `gpt-oss` family, export the preloaded Harmony encodings directory:

```bash
export GPT_OSS_ENCODINGS_PATH="/path/to/pre-downloaded/harmony-encodings"
```

`start.py` validates this path and mounts it read-only into worker containers at `/etc/encodings`.

## Prepare Offline Model Artifacts

Workers run in offline mode. Pre-download model artifacts before startup.

```bash
python3 -m pip install -U "huggingface_hub[cli]"

# Optional for gated or private models
hf auth login

export HF_HOME="/mnt/.cache/huggingface"
hf download <org>/<model-repo> --cache-dir "$HF_HOME"
```

Fallback if `hf` is not on `PATH`:

```bash
python3 -m huggingface_hub login
python3 -m huggingface_hub download <org>/<model-repo> --cache-dir "$HF_HOME"
```

## Optional `.env` Generation

Generate a non-secret `.env` interactively:

```bash
python3 utils/scripts/configure_env.py
```

If you keep non-secret values in `.env`, export them into the current shell before startup:

```bash
set -a
source .env
set +a
```

Next step: choose your runtime mode in [docs/README.md](./README.md).
