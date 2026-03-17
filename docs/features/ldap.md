<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->
# LDAP Integration

Pinned LDAP/LDAPS identity airlock for chatbot provider mode.

Availability:

- chatbot provider

## Example Configuration

```bash
export ENABLE_LDAP="true"
export LDAP_TARGET_IP="10.20.30.40"
export LDAP_TARGET_SNI="dc01.corp.local"
export ROOT_CA_PATH="/home/your-user/.ukbgpt-localhost-pki/root_ca.crt"
export LDAP_APP_PASSWORD="<service-account-password>"
```

## Use When

- user authentication must be delegated to an internal LDAP/AD service
- directory traffic must traverse a pinned, monitored egress airlock

## Behavior

- adds ldap_egress connected to docker_internal and dmz_egress
- host firewall pins outbound traffic to LDAP_TARGET_IP only
- upstream LDAPS certificate verification requires ROOT_CA_PATH

## Verify

- confirm startup reports LDAP integration enabled with pinned target
- verify frontend offers LDAP login with LDAP_SERVER_LABEL
- inspect docker logs ukbgpt_ldap_egress when troubleshooting binds or TLS

## Access

- LDAP is not host-published
- frontend reaches LDAP via ukbgpt_ldap_egress on docker_internal

## Required Variables

- `ENABLE_LDAP` (default: `false`, example: `true`): Enable LDAP identity airlock in chatbot provider mode.
- `LDAP_TARGET_IP` (example: `10.20.30.40`): Pinned private IPv4 address of the LDAP/LDAPS directory server reachable via the DMZ airlock.
- `LDAP_TARGET_SNI` (example: `dc01.corp.local`): SNI hostname expected by the LDAP server certificate during TLS verification.
- `ROOT_CA_PATH` (example: `/home/your-user/.ukbgpt-localhost-pki/root_ca.crt`): Root CA PEM used by the LDAP egress proxy to validate upstream LDAPS certificates. Testing only: run `python3 utils/scripts/create_localhost_pki.py` and use `~/.ukbgpt-localhost-pki/root_ca.crt`.
- `LDAP_APP_PASSWORD` (secret, example: `<service-account-password>`): LDAP bind account password used by OpenWebUI.

## Optional Variables

- `LDAP_SERVER_LABEL` (default: `LDAP`, example: `Corporate LDAP`): Human-readable label shown on the OpenWebUI login screen for LDAP sign-in.
- `LDAP_APP_DN` (example: `cn=admin,dc=example,dc=org`): LDAP bind DN for the service account.
- `LDAP_SEARCH_BASE` (example: `OU=Users,DC=corp,DC=local`): LDAP search base used for user lookup.
- `LDAP_ATTRIBUTE_FOR_USERNAME` (default: `uid`, example: `uid`): LDAP attribute mapped to username.
- `LDAP_ATTRIBUTE_FOR_MAIL` (default: `mail`, example: `mail`): LDAP attribute mapped to email.
- `LDAP_SEARCH_FILTER` (example: `(objectClass=user)`): Additional LDAP filter expression for user search.
- `LDAP_PROXY_CONNECT_TIMEOUT` (default: `5s`, example: `5s`): LDAP airlock connect timeout.
- `LDAP_PROXY_TIMEOUT` (default: `20s`, example: `20s`): LDAP airlock proxy timeout.

Related compose overlay:

- `compose/features/ldap.yml`
