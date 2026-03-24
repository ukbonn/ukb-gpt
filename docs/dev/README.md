# Developer Docs

This section is for contributors extending the stack.

## Why The Stack Is Split This Way

The repository keeps three layers separate on purpose:

- `compose/schema.toml`: operator-facing control plane
- `compose/*.yml`: runtime service definitions and hardening
- Python under `utils/`: only the logic that cannot live cleanly in schema or Compose

Why:

- operators get one predictable configuration surface
- security-relevant runtime behavior stays reviewable in Compose
- Python stays smaller and is reserved for validation, discovery, and render logic

## Where To Start

- General extension patterns: [Extending the stack](./extending-the-stack.md)
- Add a new app overlay: [Adding a new app](./adding-apps.md)
- Add or update model families: [Adding model families](./model-families.md)

## Important Context

- Read the [disclaimer](../disclaimer.md) and [security risk assessment](../risk_assessment.md) first
- User-facing entrypoint: [start.py](../../start.py)
- Canonical schema: [compose/schema.toml](../../compose/schema.toml)

Core code ownership:

- `utils/stack/startup.py`: validation, wizard flow, compose assembly, backend discovery
- `utils/stack/deployments.py`: startup-facing deployment selection and generated compose lifecycle
- `utils/models/deployment.py`: deployment TOML parsing, GPU validation, architecture resolution, compose rendering
