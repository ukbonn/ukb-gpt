<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Batch Client Mode

Localhost-only HTTP entrypoint for programmatic inference workloads.

Availability:

- batch client

## Example Configuration

```bash
export BATCH_CLIENT_MODE_ON="true"
export BATCH_CLIENT_LISTEN_PORT="30000"
```

## Use When

- no WebUI is required
- localhost-only /v1 API is needed for scripts and batch clients
- TLS termination at ingress is not required

## Behavior

- ingress publishes batch API only on 127.0.0.1
- direct worker port range is exposed only on localhost
- batch mode keeps all backend services on docker_internal

## Verify

- start the stack and call http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/v1/models
- confirm frontend container is absent in batch mode
- inspect runtime route map at /v1/ukbgpt/runtime

## Access

- Local API: http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/v1/
- Runtime discovery: http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/v1/ukbgpt/runtime
- Cohort feasibility (when enabled): http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/feasibility/
- Grafana (when metrics enabled): http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/grafana/

## Required Variables

- `BATCH_CLIENT_MODE_ON` (default: `false`, example: `false`): Switch between chatbot provider mode (false) and batch client mode (true).
- `BATCH_CLIENT_LISTEN_PORT` (default: `30000`, example: `30000`): Localhost ingress port used in batch client mode.

## Optional Variables

- `BATCH_CLIENT_DIRECT_PORT_START` (default: `30001`, example: `30001`): Start of localhost direct worker port range in batch mode.
- `BATCH_CLIENT_DIRECT_PORT_END` (default: `30032`, example: `30032`): End of localhost direct worker port range in batch mode.
- `BATCH_CLIENT_EGRESS_PORT` (default: `30100`, example: `30100`): Internal listen port for the api_egress container in batch mode.

## Compatible Features

- [Batch API Egress](../features/api-egress.md)
- [Metrics](../features/metrics.md)
- [Embedding Backend](../features/embedding-backend.md)
- [STT Backend](../features/stt-backend.md)

## Compatible Apps

- [Dataset Structuring App](../apps/dataset-structuring.md)
- [Cohort Feasibility App](../apps/cohort-feasibility.md)

Related compose overlay:

- `compose/modes/batch.client.yml`
