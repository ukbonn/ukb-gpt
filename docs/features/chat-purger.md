<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Chat Purger

Retention sidecar that deletes old OpenWebUI chats from local database.

Availability:

- chatbot provider

## Example Configuration

```bash
export ENABLE_CHAT_PURGER="true"
export CHAT_HISTORY_RETENTION_DAYS="180"
export CHAT_HISTORY_PURGE_INTERVAL_SECONDS="86400"
```

## Use When

- local OpenWebUI chat retention must be time-bounded
- operators need periodic cleanup without external storage

## Behavior

- runs periodic deletion against OPENWEBUI_DATA_DIR/webui.db
- defaults retention to 180 days and purge interval to 86400 seconds
- is disabled automatically in batch client mode

## Verify

- confirm startup output reports chat purger enabled
- inspect docker logs ukbgpt_chat_purger for purge activity
- verify OPENWEBUI_DATA_DIR is mounted and writable for runtime UID/GID

## Access

- chat_purger has no host-published ports
- service runs only on docker_internal

## Required Variables

- `ENABLE_CHAT_PURGER` (default: `false`, example: `true`): Enable chat retention purger sidecar in chatbot provider mode.
- `CHAT_HISTORY_RETENTION_DAYS` (default: `180`, example: `180`): Retention horizon in days for local OpenWebUI chat records.
- `CHAT_HISTORY_PURGE_INTERVAL_SECONDS` (default: `86400`, example: `86400`): Interval between chat purge executions in seconds.

Related compose overlay:

- `compose/features/chat_purger.yml`
