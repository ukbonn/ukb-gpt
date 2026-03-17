<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Cohort Feasibility App

Aggregate-only cohort explorer routed through batch ingress under /feasibility/.

Availability:

- batch client

## Example Configuration

```bash
export ENABLE_COHORT_FEASIBILITY_APP="true"
export DATASET_STRUCTURING_DATA_ROOT="/absolute/path/to/datasets_root"
```

## Use When

- aggregate cohort exploration is needed behind localhost-only batch ingress

## Behavior

- app scans DATASET_STRUCTURING_DATA_ROOT for cohort_query_no_citations.db
- route is exposed through ingress path /feasibility/
- small-count protections and aggregate-only output remain enabled

## Verify

- confirm startup reports cohort feasibility app enabled
- open http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/feasibility/
- inspect docker logs ukbgpt_cohort_feasibility if route fails

## Access

- http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/feasibility/

## Required Variables

- `ENABLE_COHORT_FEASIBILITY_APP` (default: `false`, example: `true`): Enable cohort feasibility app overlay (batch client mode only).
- `DATASET_STRUCTURING_DATA_ROOT` (example: `/absolute/path/to/datasets_root`): Host dataset root mounted into dataset structuring and cohort feasibility apps.

## Optional Variables

- `COHORT_FEASIBILITY_PORT` (default: `8090`, example: `8090`): Internal cohort feasibility service port.

Related compose overlay:

- `compose/apps/cohort_feasibility.yml`
