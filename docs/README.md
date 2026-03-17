# Operator Docs

This is the deployment hub for the single-host, network-isolated stack.

Before deploying, read:

- [Security risk assessment and disclaimer](./README_RISK_ASSESSMENT_AND_DISCLAIMER.md)

## 1. Common Setup

Start here for host preparation, TLS testing shortcuts, backend deployment selection, offline model preload, and optional `.env` generation:

- [Common setup](./setup-basics.md)

Reference pages you may want during this step:

- [Model zoo reference](../compose/models/README.md)
- [Developer docs](./dev/README.md) if you are extending the stack rather than operating it

## 2. Choose A Runtime Mode

Pick one mode first. Features and apps layer on top of that choice.

| Mode | Use when | Details |
| --- | --- | --- |
| Chatbot provider | WebUI, HTTPS ingress, browser users, optional LDAP | [Chatbot provider mode](./modes/chatbot-provider.md) |
| Batch client | Localhost-only API access, no WebUI, no TLS | [Batch client mode](./modes/batch-client.md) |

## 3. Add Optional Features

Features extend a chosen mode.

| Feature | Use when | Details |
| --- | --- | --- |
| LDAP | Chatbot users should authenticate against a pinned corporate directory | [LDAP integration](./features/ldap.md) |
| API egress | Batch mode should expose one or two pinned upstream corporate model APIs | [Batch API egress](./features/api-egress.md) |
| Metrics | You want Prometheus/Grafana and optional localhost scrape tunnels | [Metrics](./features/metrics.md) |
| Chat purger | You want automatic deletion of old OpenWebUI chat history | [Chat purger](./features/chat-purger.md) |

## 4. Add Optional Apps

Apps are separate overlay services documented independently from the core stack.

| App | Use when | Details |
| --- | --- | --- |
| Dictation | You want a dedicated internal STT UI under `/dictation/` | [Dictation app](./apps/dictation.md) |
| Cohort feasibility | You want the aggregate-only browser explorer under `/feasibility/` | [Cohort feasibility app](./apps/cohort-feasibility.md) |
| Dataset structuring | You want the internal utility container for dataset jobs | [Dataset structuring app](./apps/dataset-structuring.md) |

## 5. Validate And Start

Optional helper for generating a non-secret `.env`:

```bash
python3 utils/scripts/configure_env.py
```

If you keep non-secret values in `.env`, export them into the current shell first:

```bash
set -a
source .env
set +a
```

Recommended startup flow:

```bash
python3 start.py validate
sudo -v
python3 start.py up
```

Canonical command set:

- `python3 start.py wizard`
- `python3 start.py up`
- `python3 start.py validate`
- `python3 stop.py`

## 6. Operate And Troubleshoot

After startup, use the lifecycle guide for endpoint checks, logs, diagnostics, and shutdown:

- [Lifecycle guide](./lifecycle.md)

Typical paths:

- Minimal chatbot deployment: [Common setup](./setup-basics.md) -> [Chatbot provider mode](./modes/chatbot-provider.md) -> [Lifecycle guide](./lifecycle.md)
- Minimal batch deployment: [Common setup](./setup-basics.md) -> [Batch client mode](./modes/batch-client.md) -> [Lifecycle guide](./lifecycle.md)
- Chatbot with identity and retention: add [LDAP integration](./features/ldap.md) and [Chat purger](./features/chat-purger.md)
- Batch with upstream routing: add [Batch API egress](./features/api-egress.md)

Generated mode, feature, and app pages are reference docs. Use this hub for onboarding and deployment flow.
