<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Dataset Structuring App

Internal utility container for on-demand dataset processing jobs.

Availability:

- batch client

## Example Configuration

```bash
export ENABLE_DATASET_STRUCTURING_APP="true"
export DATASET_STRUCTURING_DATA_ROOT="/absolute/path/to/datasets_root"
export API_KEY="<your_llm_api_key>"
export EMBEDDING_API_KEY="<your_embedding_api_key>"
```

## Use When

- internal batch preprocessing jobs should run inside the managed stack
- dataset processing should reuse local batch ingress discovery and embeddings routes

## Behavior

- container stays alive and jobs are triggered with docker exec
- requires DATASET_STRUCTURING_DATA_ROOT mount
- CPU and memory defaults are auto-derived when not explicitly provided

## Verify

- confirm startup reports dataset structuring app enabled
- run a smoke docker exec job against mounted dataset root
- inspect docker logs ukbgpt_dataset_structuring on failures

## Access

- no direct browser endpoint
- execute jobs via docker exec ukbgpt_dataset_structuring ...

## Required Variables

- `ENABLE_DATASET_STRUCTURING_APP` (default: `false`, example: `true`): Enable dataset structuring utility app (batch client mode only).
- `DATASET_STRUCTURING_DATA_ROOT` (example: `/absolute/path/to/datasets_root`): Host dataset root mounted into dataset structuring and cohort feasibility apps.
- `API_KEY` (secret, example: `<your_llm_api_key>`): API key used by dataset structuring app for LLM requests.
- `EMBEDDING_API_KEY` (secret, example: `<your_embedding_api_key>`): API key used by dataset structuring app for embedding requests.

## Optional Variables

- `DATASET_STRUCTURING_CPUSET` (example: `2-15`): Optional CPU affinity override for dataset structuring app.
- `DATASET_STRUCTURING_MEM_LIMIT` (example: `48g`): Optional memory limit override for dataset structuring app.
- `DATASET_STRUCTURING_NOFILE_SOFT` (default: `8192`, example: `8192`): Soft nofile ulimit for dataset structuring app.
- `DATASET_STRUCTURING_NOFILE_HARD` (default: `8192`, example: `8192`): Hard nofile ulimit for dataset structuring app.

Related compose overlay:

- `compose/apps/dataset_structuring.yml`
