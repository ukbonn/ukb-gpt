# Tests

The suite now follows a single execution model: **managed stacks only**.
Pytest starts and tears down deterministic test stacks via fixtures in `tests/conftest.py`.

## Strategy At A Glance

Execution is decided by three things:

1. **Collected files**: controlled by `pytest.ini` and optional path arguments.
2. **Markers**: controlled by `-m "...boolean expression..."`.
3. **Fixture wiring**: tests use `chatbot_provider_stack`, `batch_client_stack`, or the selector fixture `stack`.

Primary marker groups:

- `integration`: functional full-stack behavior checks.
- `isolation`: network and hardening boundary checks.
- `chatbot_provider` / `batch_client`: runtime-mode expectations.
- `destructive`: intentionally disruptive tests.

Runtime modes:

- **Chatbot provider**: LDAP + metrics enabled.
- **Batch client**: API egress enabled, no frontend.

## Prerequisites

Use the repo virtualenv for all commands:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -r requirements.txt
```

## How Test Selection Works

1. **Collection root**  
`pytest.ini` sets `testpaths = tests`, so collection starts from `tests/`.

2. **Path filter (optional)**  
If you pass `tests/isolation`, only that subtree is considered.

3. **Marker filter (optional)**  
`-m` applies a boolean expression to markers.

4. **Fixture resolution**  
After selection, fixtures determine which managed stack is started.

## Fixture Model

`tests/conftest.py` provides:

- `chatbot_provider_stack`: starts/tears down chatbot provider mode stack.
- `batch_client_stack`: starts/tears down batch client mode stack.
- `stack`: selector fixture for isolation tests.

`stack` selection behavior:

- if the active marker expression is batch-client-only, it uses `batch_client_stack`
- otherwise it uses `chatbot_provider_stack`

Many integration tests use `chatbot_provider_stack` or `batch_client_stack` directly, so their mode is explicit in test code.

## Managed Lifecycle Behavior

Managed fixtures perform:

- Preflight cleanup of prior containers/networks.
- PKI generation for test certificates.
- Stack startup through `start.py`.
- Log capture under `tests/logs/`.
- Full teardown in fixture `finally` blocks.

`start.py` is now fail-fast for startup safety gates (including firewall enforcement). If those checks fail, startup exits non-zero and tests fail early.

## Common Commands

Managed stack suites:

```bash
sudo -v
sudo -E "$(pwd)/.venv/bin/python" -m pytest -m chatbot_provider
sudo -E "$(pwd)/.venv/bin/python" -m pytest -m batch_client
```

Stream `start.py` output during managed startup:

```bash
TEST_STACK_VERBOSE=true sudo -E "$(pwd)/.venv/bin/python" -m pytest -m chatbot_provider
TEST_STACK_VERBOSE=true sudo -E "$(pwd)/.venv/bin/python" -m pytest -m batch_client
```

Inspect what would run (without executing tests):

```bash
./.venv/bin/python -m pytest --collect-only -q -m chatbot_provider
./.venv/bin/python -m pytest --collect-only -q -m batch_client
```

## Logs

Managed runs store stack logs under:

```text
tests/logs/
```
