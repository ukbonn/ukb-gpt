<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Embedding Backend

Dedicated embedding worker backend selected via EMBEDDING_MODEL_DEPLOYMENT_CONFIG.

Availability:

- chatbot provider
- batch client

## Example Configuration

```bash
export EMBEDDING_MODEL_DEPLOYMENT_CONFIG="examples/model_deployments/gte-qwen2.single-gpu.toml"
```

## Use When

- retrieval embeddings should run on a dedicated worker set
- embedding model lifecycle should be separated from the primary LLM backend

## Behavior

- setting EMBEDDING_MODEL_DEPLOYMENT_CONFIG enables the embedding backend
- startup appends a runtime-generated embedding compose file when the deployment config is present
- the embedding backend is added to OpenWebUI's OPENAI_API_BASE_URLS provider list for external /api/models and /api/embeddings access
- OpenWebUI internal RAG embeddings continue to use RAG_OPENAI_API_BASE_URL

## Verify

- confirm startup output reports embedding backend enabled
- inspect runtime discovery output for embedding workers and EMBEDDING_ENDPOINT
- verify OpenWebUI exposes the embedding model through /api/models and accepts /api/embeddings requests

## Access

- embedding workers are internal-only on docker_internal
- external clients reach them through OpenWebUI's HTTPS API rather than directly

## Required Variables

- `EMBEDDING_MODEL_DEPLOYMENT_CONFIG` (example: `examples/model_deployments/gte-qwen2.single-gpu.toml`): Deployment config for embedding workers (repo-relative or absolute path).

Related compose overlay:

- `compose/features/embedding_backend.yml`
