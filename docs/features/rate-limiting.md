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
- frontend browser traffic and backend API traffic should share the same model backend without API bursts making the UI feel sluggish

## Behavior

- adds an internal pipelines sidecar connected only to docker_internal
- frontend keeps its direct backend path by default and appends the pipelines connection only when enabled
- rate limiting applies to OpenWebUI provider traffic, including external /api/embeddings requests when the embedding backend is enabled
- pipeline valves remain the admin-facing place to choose adaptive or static limiting
- for smooth coexistence of frontend/browser traffic and backend API traffic, use all three pieces together: an adaptive rate-limit filter with settings tuned to backend capacity, priority injection into the request body (`priority=0` for browser traffic and `priority=1` for API traffic), and vLLM `--scheduling-policy=priority` so lower-numbered priorities are served first
- the priority split depends on a local OpenWebUI/OpenWebUI Pipelines patch carried in this repo; this is not a vLLM body-forwarding limitation, but an OpenWebUI/Pipelines request-metadata forwarding gap
- upstream OpenWebUI does not currently forward enough request metadata into pipeline inlet filters for a reliable browser-vs-API decision, so this repo forwards sanitized request context to the pipeline and the adaptive rate-limit pipeline injects `priority` into the request body before the request reaches vLLM
- we plan to turn that local patch into an upstream PR, but until that lands you should stay on the repo-pinned OpenWebUI/OpenWebUI Pipelines versions; future upstream releases may change the relevant code paths and could break this integration until the patch is refreshed or no longer needed

## Verify

- confirm startup output reports Rate Limiting enabled
- check /api/v1/pipelines/list in OpenWebUI for the internal http://pipelines:9099/v1 registration
- inspect docker logs ukbgpt_pipelines when validating adaptive/static blocking behavior
- inspect docker logs ukbgpt_pipelines for `Priority decision ident=... request_priority=0|1` when validating browser-vs-API separation
- inspect `docker inspect ukbgpt_worker_0` or the generated model compose to confirm `--scheduling-policy=priority` is present on the vLLM worker command

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
