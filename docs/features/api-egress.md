<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Batch API Egress

Pinned HTTPS proxy for additional internal LLM and embedding APIs in batch mode.

Availability:

- batch client

## Example Configuration

```bash
export ENABLE_API_EGRESS="true"
export ROOT_CA_PATH="/home/your-user/.ukbgpt-localhost-pki/root_ca.crt"
```

## Use When

- batch mode should route requests to additional pinned internal model APIs
- runtime egress must remain IP-pinned and TLS-validated

## Behavior

- enables api_egress connected to docker_internal and dmz_egress
- startup rewrites upstream addresses to pinned IPs and keeps SNI/Host explicitly configured
- if ENABLE_API_EGRESS is unset, startup keeps compatibility by inferring enablement from address variables

## Verify

- confirm startup output reports batch API egress enabled
- call local /v1/models and verify responses can include api_egress-backed upstreams
- inspect docker logs ukbgpt_api_egress for upstream connection failures

## Access

- api_egress is not host-published
- local clients continue to use batch ingress on 127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>

## Required Variables

- `ENABLE_API_EGRESS` (default: `false`, example: `true`): Enable pinned batch API egress feature.
- `ROOT_CA_PATH` (example: `/home/your-user/.ukbgpt-localhost-pki/root_ca.crt`): Root CA PEM used by api_egress to verify TLS for pinned additional API upstreams. Testing only: run `python3 utils/scripts/create_localhost_pki.py` and use `~/.ukbgpt-localhost-pki/root_ca.crt`.

## Optional Variables

- `BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS` (example: `https://gpt.ukb.intern`): HTTPS origin of the additional LLM API endpoint reachable through api_egress.
- `BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_IP` (example: `10.20.24.206`): Pinned private IPv4 for the additional LLM API origin.
- `BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_SNI` (example: `gpt.corp.internal`): Optional explicit TLS SNI hostname for additional upstream LLM API.
- `BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS` (example: `https://embeddings.ukb.intern`): HTTPS origin of the additional embedding API endpoint reachable through api_egress.
- `BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_IP` (example: `10.20.24.207`): Pinned private IPv4 for the additional embedding API origin.
- `BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_SNI` (example: `embeddings.corp.internal`): Optional explicit TLS SNI hostname for additional upstream embedding API.
- `BATCH_CLIENT_EGRESS_PORT` (default: `30100`, example: `30100`): Internal listen port for the api_egress container in batch mode.
- `API_EGRESS_INTERNAL_IP` (default: `172.16.238.12`, example: `172.16.238.12`): Static docker_internal identity for api_egress.
- `API_EGRESS_CLIENT_MAX_BODY_SIZE` (default: `100M`, example: `100M`): Maximum request body size accepted by api_egress proxy.
- `API_EGRESS_PROXY_CONNECT_TIMEOUT` (default: `10s`, example: `10s`): API egress connect timeout.
- `API_EGRESS_PROXY_READ_TIMEOUT` (default: `3600s`, example: `3600s`): API egress upstream read timeout.
- `API_EGRESS_PROXY_SEND_TIMEOUT` (default: `3600s`, example: `3600s`): API egress upstream send timeout.

Related compose overlay:

- `compose/features/api_egress.yml`
