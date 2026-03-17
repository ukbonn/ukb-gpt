# Extending The Stack

This stack is designed so most changes stay declarative.

## Why It Works This Way

The preferred order is:

1. Compose for runtime behavior
2. `compose/schema.toml` for operator inputs and wizard flow
3. Python only when the first two are not enough

That keeps the system easier to review, simpler to operate, and less likely to hide security-sensitive behavior in ad hoc code.

## What Belongs Where

Put changes in [compose/schema.toml](../../compose/schema.toml) when you need:

- new operator variables
- defaults, examples, and validators
- wizard prompt order
- overlay selection rules

Put changes in Compose when you need:

- services
- images or builds
- networks
- volumes
- resource limits
- hardening choices

Put changes in Python only when you need:

- host-path or file-content validation
- host-state discovery
- derived defaults
- runtime discovery output
- deployment rendering logic that schema/Compose cannot express cleanly

## Safe Defaults

Prefer:

- `docker_internal` only
- ingress routing instead of new host ports
- `hardened_common`
- narrow writable paths
- no direct outbound network access

Treat routed egress or broader privileges as explicit exceptions, not normal app behavior.

## Contributor Checklist

1. Add or update the Compose overlay.
2. Register it in [compose/schema.toml](../../compose/schema.toml).
3. Add or update the variable bindings.
4. Add operator docs under `docs/`.
5. Add Python only if the behavior cannot stay declarative.
6. Add or update tests.
7. Re-check the zero-exfiltration posture before considering the change done.

## Main Python Entry Points

- [start.py](../../start.py): CLI entrypoint
- [startup.py](../../utils/stack/startup.py): validation, wizard flow, compose assembly
- [deployments.py](../../utils/stack/deployments.py): startup-facing deployment orchestration
- [deployment.py](../../utils/models/deployment.py): model deployment parsing and rendering
- [schema.py](../../utils/stack/schema.py): schema parsing rules
