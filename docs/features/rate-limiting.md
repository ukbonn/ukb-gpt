<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Rate Limiting

Optional OpenWebUI Pipelines sidecar for static or adaptive request rate limiting.

Availability:

- chatbot provider

## Example Configuration

```bash
export ENABLE_RATE_LIMITING="true"
```

## Use When

- chatbot mode should apply static or adaptive request limits before requests reach the model backend
- operators want OpenWebUI to manage rate limiting through the bundled Pipelines service

## Behavior

- adds an internal pipelines sidecar connected only to docker_internal
- frontend keeps its direct backend path by default and appends the pipelines connection only when enabled
- rate limiting applies to OpenWebUI provider traffic, including external /api/embeddings requests when the embedding backend is enabled
- pipeline valves remain the admin-facing place to choose adaptive or static limiting

## Verify

- confirm startup output reports Rate Limiting enabled
- check /api/v1/pipelines/list in OpenWebUI for the internal http://pipelines:9099/v1 registration
- inspect docker logs ukbgpt_pipelines when validating adaptive/static blocking behavior

## Access

- pipelines is not host-published
- frontend reaches the pipelines sidecar only on docker_internal

## Required Variables

- `ENABLE_RATE_LIMITING` (default: `false`, example: `true`): Enable the OpenWebUI Pipelines sidecar and adaptive/static rate limiting in chatbot provider mode.

## Optional Variables

- `OPENWEBUI_PIPELINES_API_KEY` (secret, example: `$(openssl rand -hex 16)`): Optional internal API key override for the bundled OpenWebUI Pipelines service.
- `ADAPTIVE_RATE_LIMIT_METRICS_URL` (example: `http://worker_0:5000/metrics`): Optional default metrics endpoint used by the adaptive rate-limit pipeline.

Related compose overlay:

- `compose/features/rate_limiting.yml`
