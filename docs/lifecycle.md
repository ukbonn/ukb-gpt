# Lifecycle Guide

This page covers validation, startup, verification, shutdown, and diagnostics after you have chosen a mode and any optional overlays.

## Validate And Start

Recommended flow:

```bash
python3 start.py validate
sudo -v
python3 start.py up
```

Startup behavior:

- `start.py` is fail-fast: validation, startup assembly, and firewall setup must succeed before services stay up
- host firewall rules are applied before services are started
- startup logs are written to `start.log` in the repo root and overwritten on each start
- if `./.venv/bin/python` exists, `start.py` re-launches itself with that interpreter unless `UKBGPT_SKIP_VENV_REEXEC=true`

Chatbot provider mode note:

- OpenWebUI creates `webui.db` automatically in `OPENWEBUI_DATA_DIR`
- `OPENWEBUI_DATA_DIR` must be set explicitly

## Verify Access

Chatbot provider mode:

- WebUI: `https://<SERVER_NAME>/`
- Grafana when metrics are enabled: `https://<SERVER_NAME>/grafana/`
- Dictation app when enabled: `https://<SERVER_NAME>/dictation/`

Batch client mode:

- Local API: `http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/v1/`
- Runtime discovery: `http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/v1/ukbgpt/runtime`
- Cohort feasibility app when enabled: `http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/feasibility/`
- Grafana when metrics are enabled: `http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/grafana/`

Host-local scrape tunnel when `ENABLE_METRICS_FORWARDING=true`:

- ingress metrics: `http://127.0.0.1:8001/metrics`
- worker 0 metrics: `http://127.0.0.1:5000/metrics`
- additional worker metrics: ports increment from `5000`

Dataset structuring app note:

- it is an internal utility container, not a browser endpoint
- run jobs with `docker exec` after the stack is up

## Stop And Cleanup

Use the shutdown helper:

```bash
python3 stop.py
```

To also remove project-scoped Docker volumes created by the stack:

```bash
python3 stop.py --volumes
```

`stop.py` tears down the fixed Compose project (`ukbgpt`) and cleans up leftover project networks by Docker label.

## Logs And Diagnostics

- startup logs: `start.log` in the repo root
- container logs: `docker logs <container>`
- startup checks and firewall enforcement output: stdout during startup
- batch runtime inspection: `http://127.0.0.1:<BATCH_CLIENT_LISTEN_PORT>/v1/ukbgpt/runtime`

## Offline Notes

- worker images run with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`
- model artifacts must already exist under `HF_HOME`
- in fully air-gapped deployments, preload required Docker images before `python3 start.py up`
