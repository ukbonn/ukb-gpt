<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# STT Backend

Dedicated STT worker backend selected via STT_MODEL_DEPLOYMENT_CONFIG.

Availability:

- chatbot provider
- batch client

## Example Configuration

```bash
export STT_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-mini-4b.single-gpu.toml"
```

## Use When

- speech-to-text transcription should run on dedicated workers
- dictation app should use an internal STT backend

## Behavior

- setting STT_MODEL_DEPLOYMENT_CONFIG enables the STT backend
- startup appends a runtime-generated STT compose file when the deployment config is present
- OpenWebUI keeps STT on AUDIO_STT_OPENAI_API_BASE_URL and AUDIO_STT_MODEL for transcription, while specially marked multimodal STT families may also be appended to OPENAI_API_BASE_URLS for chat and /api/models exposure

## Verify

- confirm startup output reports STT backend enabled
- inspect runtime discovery output for stt workers and STT_ENDPOINT
- confirm startup output shows the dedicated STT backend wiring

## Access

- stt workers are internal-only on docker_internal
- OpenWebUI transcription traffic stays on the dedicated audio backend path

## Required Variables

- `STT_MODEL_DEPLOYMENT_CONFIG` (example: `examples/model_deployments/voxtral-mini-4b.single-gpu.toml`): Deployment config for STT workers (repo-relative or absolute path).

Related compose overlay:

- `compose/features/stt_backend.yml`
