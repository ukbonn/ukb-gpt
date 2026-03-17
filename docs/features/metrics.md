<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Metrics

Exporter, Prometheus, Grafana, and optional localhost scrape tunnels.

Availability:

- chatbot provider
- batch client

## Example Configuration

```bash
export ENABLE_INTERNAL_METRICS="true"
```

## Use When

- runtime metrics and dashboards are required
- operators need Grafana access through ingress

## Behavior

- adds exporter, Prometheus, and Grafana services
- Grafana is routed through ingress under /grafana/
- Grafana anonymous access is disabled; first login is the upstream default `admin` / `admin` unless an existing grafana-data volume already has a changed password
- optional localhost scrape tunnels are controlled by ENABLE_METRICS_FORWARDING

## Verify

- confirm startup output reports internal metrics enabled
- open the Grafana route for the active mode
- inspect docker logs ukbgpt_prometheus when troubleshooting scrape configuration

## Access

- Chatbot provider mode: https://<SERVER_NAME>/grafana/
- Batch mode: http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/grafana/
- Optional scrape tunnel: http://127.0.0.1:8001/metrics

## Required Variables

- `ENABLE_INTERNAL_METRICS` (default: `false`, example: `true`): Enable exporter + Prometheus + Grafana overlays.

## Optional Variables

- `ENABLE_METRICS_FORWARDING` (default: `false`, example: `true`): Expose localhost scrape tunnels for ingress/worker metrics.

Related compose overlay:

- `compose/features/metrics.yml`
