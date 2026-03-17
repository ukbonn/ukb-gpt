# Adding Model Families

This guide is for contributors extending the model zoo under [compose/models](../../compose/models).

## Why Model Families And Deployment Specs Are Split

The stack separates:

- model family metadata: what a model is and how one worker should look
- deployment spec: how that family is placed on this host

Why:

- the model zoo stays reusable across different GPU layouts
- host-specific worker topology is not committed into the repo
- startup can validate local GPUs before rendering Compose

If you are only adding a new model family, you usually do not need to touch Python.

## What To Add

Add one directory per model family:

- `compose/models/llm/<slug>/`
- `compose/models/embedding/<slug>/`
- `compose/models/stt/<slug>/`

Each family directory must contain:

- `base.yml`: static Docker scaffold only
- `model.toml`: canonical metadata and runtime defaults

Also add:

- an example deployment under `examples/model_deployments/`
- test fixtures under `tests/model_deployments/`

## What Goes Where

Keep `base.yml` for static Docker shape only:

- `extends`
- build context / Dockerfile
- fixed `image` or `entrypoint` when needed

Keep `model.toml` for the runtime baseline:

- model metadata
- worker command defaults
- default environment and build args
- default worker image metadata (`runtime.default_vllm_openai_image`) when the family uses `VLLM_OPENAI_IMAGE`
- GPU architecture presets
- model-specific variable references

Do not put mutable runtime defaults back into `base.yml`.

## When Python Changes Are Needed

Only touch Python when model deployment behavior changes, for example:

- new deployment validation rules
- new architecture preset behavior
- generated compose behavior changes

Relevant files:

- [deployment.py](../../utils/models/deployment.py)
- [deployments.py](../../utils/stack/deployments.py)
- [startup.py](../../utils/stack/startup.py)

## Contributor Checklist

1. Add `compose/models/<role>/<slug>/base.yml`.
2. Add `compose/models/<role>/<slug>/model.toml`.
3. Add an example deployment under `examples/model_deployments/`.
4. Add new schema variables in [compose/schema.toml](../../compose/schema.toml) only if the family needs them.
5. Add test fixtures under `tests/model_deployments/`.
6. Extend [tests/isolation/test_model_deployment.py](../../tests/isolation/test_model_deployment.py) when behavior changes.
7. Regenerate docs with `./.venv/bin/python utils/scripts/build_docs.py`.

Startup-facing deployment behavior changes should also be checked against:

- [tests/isolation/test_start_utils_worker_images.py](../../tests/isolation/test_start_utils_worker_images.py)
- [tests/isolation/test_compose_static.py](../../tests/isolation/test_compose_static.py)
