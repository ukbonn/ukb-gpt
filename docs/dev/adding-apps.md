# Adding A New App

Apps are optional services layered on top of a selected runtime mode.

## Why Use An App Overlay

Use an app when the new behavior is a distinct end-user or utility service, not a change to the core stack itself.

Typical app shapes:

- browser-facing tools routed through ingress
- internal utility containers run through `docker exec` or internal APIs

## Minimum Steps

1. Create `compose/apps/<name>.yml`.
2. Add an `app.*` overlay entry in [compose/schema.toml](../../compose/schema.toml).
3. Add `ENABLE_<APP>` and any app-specific variables in the same schema file.
4. Add operator docs in `docs/apps/`.
5. Add startup validation only if the schema and Compose cannot express the requirement cleanly.

## Security And Ingress Defaults

Prefer this baseline:

- attach only to `docker_internal`
- inherit from [compose/hardening.yml](../../compose/hardening.yml)
- avoid host `ports:`
- route browser-facing apps through ingress
- keep egress disabled unless there is a deliberate exception design

If the app must be reachable under a path such as `/my-app/`, update ingress and document that access path in `docs/apps/<name>.md`.

## Good Examples To Copy

- [compose/apps/dictation.yml](../../compose/apps/dictation.yml)
- [compose/apps/dataset_structuring.yml](../../compose/apps/dataset_structuring.yml)

## When Python Is Actually Needed

Only touch Python when the app needs something like:

- host-path validation
- derived defaults
- compatibility checks beyond simple schema conditions
- resolved backend discovery values

Relevant file when that happens:

- [startup.py](../../utils/stack/startup.py)
