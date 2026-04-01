<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# TTS Backend

Dedicated TTS worker backend selected via TTS_MODEL_DEPLOYMENT_CONFIG.

Availability:

- chatbot provider
- batch client

## Example Configuration

```bash
export TTS_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/voxtral-4b-tts.single-gpu.toml"
```

## Use When

- text-to-speech generation should run on dedicated workers
- dictation app should read translated text aloud through an internal TTS backend

## Behavior

- setting TTS_MODEL_DEPLOYMENT_CONFIG enables the TTS backend
- startup appends a runtime-generated TTS compose file when the deployment config is present
- TTS remains internal-only and is not added to OpenWebUI OPENAI_API_BASE_URLS

## Verify

- confirm startup output reports TTS backend enabled
- inspect runtime discovery output for tts workers and TTS_ENDPOINT
- issue a POST to /v1/audio/speech against the selected route

## Access

- tts workers are internal-only on docker_internal
- dictation translated-text read-aloud uses the dedicated TTS backend when configured

## Required Variables

- `TTS_MODEL_DEPLOYMENT_CONFIG` (example: `examples/model_deployments/voxtral-4b-tts.single-gpu.toml`): Deployment config for TTS workers (repo-relative or absolute path).

## Optional Variables

- `TTS_MODEL_ID` (default: `mistralai/Voxtral-4B-TTS-2603`, example: `mistralai/Voxtral-4B-TTS-2603`): Model id presented to dictation/TTS clients.

Related compose overlay:

- `compose/features/tts_backend.yml`
