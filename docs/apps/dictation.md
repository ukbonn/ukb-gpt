<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Dictation App

Dedicated internal STT UI exposed through ingress under /dictation/.

Availability:

- chatbot provider

## Example Configuration

```bash
export ENABLE_DICTATION_APP="true"
```

## Use When

- a dedicated dictation UI is required in chatbot provider mode

## Behavior

- dictation is exposed only via ingress path /dictation/
- ENABLE_DICTATION_APP requires STT_MODEL_DEPLOYMENT_CONFIG
- STT-only backend mode is supported only when dictation is enabled

## Verify

- confirm startup reports dictation enabled
- open https://<SERVER_NAME>/dictation/
- if startup fails, verify STT_MODEL_DEPLOYMENT_CONFIG is set

## Access

- Dictation: https://<SERVER_NAME>/dictation/

## Required Variables

- `ENABLE_DICTATION_APP` (default: `false`, example: `true`): Enable dictation app overlay (chatbot provider mode only).

## Optional Variables

- `STT_MODEL_ID` (default: `mistralai/Voxtral-Mini-4B-Realtime-2602`, example: `mistralai/Voxtral-Mini-4B-Realtime-2602`): Model id presented to dictation/STT clients.

Related compose overlay:

- `compose/apps/dictation.yml`
