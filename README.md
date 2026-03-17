# UKB-GPT

Single-host, isolation-first GenAI stack for sensitive data processing.

This repository is for running local chat, embedding, and STT backends with strict network isolation and pinned egress exceptions only where explicitly configured.

Read the threat model and disclaimer first:

- [Security risk assessment and disclaimer](docs/README_RISK_ASSESSMENT_AND_DISCLAIMER.md)

## Quick Start

Set up the local Python environment once:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -r requirements.txt
```

Fastest safe path:

```bash
python3 start.py wizard
python3 start.py up
```

What the wizard does:

- writes non-secret settings to `.env`
- keeps secrets out of `.env`
- helps you choose runtime mode, optional features/apps, and backend deployments

Before startup, make sure model artifacts already exist locally. Normal runtime downloads are not part of the intended path.

## Where To Go Next

- New operator: [Operational docs hub](docs/README.md)
- Common prerequisites and backend selection: [Common setup](docs/setup-basics.md)
- Tests: [tests/README.md](tests/README.md)
- Security disclosure: [SECURITY.md](SECURITY.md)

## Useful Commands

```bash
python3 start.py validate
python3 start.py wizard --start
python3 start.py up --env-file /path/to/.env
python3 stop.py
```

Secrets can be exported in the shell or provided via `*_FILE` variables such as `CERTIFICATE_KEY_FILE`. Do not set both `VAR` and `VAR_FILE` for the same secret.
