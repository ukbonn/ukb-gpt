<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# Chatbot Provider Mode

Interactive deployment with OpenWebUI and HTTPS ingress.

Availability:

- chatbot provider

## Example Configuration

```bash
export BATCH_CLIENT_MODE_ON="false"
export CERTIFICATE_KEY="$(cat /path/to/server.key)"
export SSL_CERT_PATH="/home/your-user/.ukbgpt-localhost-pki/fullchain.pem"
export WEBUI_SECRET_KEY="$(openssl rand -hex 32)"
export OPENWEBUI_DATA_DIR="/var/lib/ukbgpt/openwebui-data"
```

## Use When

- browser access through OpenWebUI is required
- HTTPS termination is required at ingress
- LDAP-backed user authentication may be enabled
- the optional dictation UI should be reachable via ingress

## Behavior

- ingress publishes 0.0.0.0:80 and 0.0.0.0:443
- OpenWebUI local state is persisted under OPENWEBUI_DATA_DIR
- OPENAI_API_BASE_URLS carries the primary LLM backend and, when enabled, the embedding backend for external OpenWebUI API usage
- STT remains wired through the dedicated AUDIO_STT_OPENAI_API_BASE_URL path rather than OPENAI_API_BASE_URLS
- startup fails fast when TLS key/cert or WEBUI_SECRET_KEY is missing

## Verify

- start the stack and confirm WebUI is reachable at https://<SERVER_NAME>/
- confirm startup output reports chatbot provider mode and no batch API route
- if OpenWebUI does not start, inspect OPENWEBUI_DATA_DIR permissions and ownership

## Access

- WebUI: https://<SERVER_NAME>/
- Grafana (when metrics enabled): https://<SERVER_NAME>/grafana/
- Dictation (when enabled): https://<SERVER_NAME>/dictation/

## Required Variables

- `BATCH_CLIENT_MODE_ON` (default: `false`, example: `false`): Switch between chatbot provider mode (false) and batch client mode (true).
- `CERTIFICATE_KEY` (secret, example: `$(cat /path/to/server.key)`): TLS private key PEM content for ingress (chatbot provider mode).
- `SSL_CERT_PATH` (example: `/home/your-user/.ukbgpt-localhost-pki/fullchain.pem`): Absolute host path to the ingress TLS fullchain PEM (leaf + intermediates). Testing only: run `python3 utils/scripts/create_localhost_pki.py` and use `~/.ukbgpt-localhost-pki/fullchain.pem`.
- `WEBUI_SECRET_KEY` (secret, example: `$(openssl rand -hex 32)`): OpenWebUI application secret key.
- `OPENWEBUI_DATA_DIR` (example: `/var/lib/ukbgpt/openwebui-data`): Host path mounted to OpenWebUI data storage.

## Optional Variables

- `OPENWEBUI_RUNTIME_UID` (example: `1000`): Runtime UID for OpenWebUI container bind-path ownership alignment.
- `OPENWEBUI_RUNTIME_GID` (example: `1000`): Runtime GID for OpenWebUI container bind-path ownership alignment.
- `ENABLE_API_KEYS` (default: `1`, example: `1`): Allow in general OpenWebUI API key usage.
- `USER_PERMISSIONS_FEATURES_API_KEYS` (default: `1`, example: `1`): Allow also default users OpenWebUI API key usage.

## Compatible Features

- [Rate Limiting](../features/rate-limiting.md)
- [LDAP Integration](../features/ldap.md)
- [Metrics](../features/metrics.md)
- [Chat Purger](../features/chat-purger.md)
- [Embedding Backend](../features/embedding-backend.md)
- [STT Backend](../features/stt-backend.md)
- [TTS Backend](../features/tts-backend.md)

## Compatible Apps

- [Dictation App](../apps/dictation.md)

Related compose overlay:

- `compose/modes/frontend.provider.yml`
