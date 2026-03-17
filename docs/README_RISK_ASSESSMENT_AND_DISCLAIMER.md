# Security Risk Assessment and Disclaimer

**Project:** UKB-GPT (single-host, isolation-first LLM stack)

| Field | Value |
| --- | --- |
| Document | Security Risk Assessment and Disclaimer |
| Version | 0.4 |
| Last updated | 2026-03-16 |
| Intended audience | Security reviewers, deployers, auditors |
| Security contact | See `SECURITY.md` |

> This document describes the intended security posture of this repository. It is not a certification, warranty, or substitute for an organization-specific security review, DPIA, or penetration test.

## 1. Executive Summary

The primary security objective of this repository is **Zero Exfiltration**:

- Prompts, completions, embeddings, model artifacts, and derived sensitive data should not leave the host over the network.
- The only intended exceptions are explicit, pinned airlocks:
  - LDAP airlock: internal frontend traffic to a single pinned LDAPS target (`LDAP_TARGET_IP:636`)
  - Batch API airlock: internal batch traffic to a pinned HTTPS target set (`EGRESS_TARGET_IPS` / generated firewall rules), including exact TCP port pinning

The main design assumption is **assume breach at the container level**. Internal application containers are treated as potentially compromised. Network enforcement is therefore placed primarily in the host firewall, not inside containers.

## 2. What This Repository Is and Is Not

This repository is:

- a security-focused proof of concept for self-hosted LLM deployments
- an orchestration and isolation layer around third-party components such as Nginx, OpenWebUI, vLLM, Prometheus, and Grafana
- designed for local or organizationally controlled infrastructure

This repository is not:

- a security certification
- a managed service
- a guarantee against all compromise scenarios
- a medical device

Any institutional approval of a deployment is environment-specific and does not transfer automatically to another organization.

## 3. Security Model

### 3.1 Security goals

The implementation is built around these goals:

1. No normal direct internet egress from internal application containers
2. Minimal lateral movement between services
3. Host-enforced network restrictions
4. Explicit and auditable exceptions only
5. Least-privilege container runtime defaults

### 3.2 Trust boundaries

- **Trusted:** the host OS and host administrators
- **Potentially untrusted:** application containers, model runtimes, web-facing components, uploaded artifacts
- **Residual-risk boundary:** any explicitly allowed airlock target

### 3.3 Main technical controls

#### Docker micro-segmentation

The stack is split across dedicated Docker bridges:

- `docker_internal`: internal-only service network, no normal routed egress
- `dmz_ingress`: ingress-facing bridge for the host-exposed reverse proxy
- `dmz_egress`: optional routed bridge used only for explicit airlocks

#### Host firewall enforcement

`start.py` invokes [`security_helpers/apply_host_firewall.py`](../security_helpers/apply_host_firewall.py), which installs managed `iptables` / `ip6tables` rules through `DOCKER-USER`.

Current intent:

- `br-internal`: drop forwarded traffic leaving the internal vault
- `br-dmz-ingress`: allow only the response path needed for ingress behavior, then drop the rest
- `br-dmz-egress`: allow only explicit pinned `tcp + destination IP + destination port` rules, then drop the rest

This is the authoritative exfiltration boundary. The kill-switch monitor is only a secondary detector.

#### Ingress controls

- Chatbot provider mode:
  - ingress is the only normal host entry point
  - default deployment binds HTTP `:80` and HTTPS `:443`
  - access is additionally constrained by Nginx ACLs and TLS configuration
- Batch client mode:
  - ingress is localhost-only
  - batch API and diagnostics are exposed only on loopback-bound ports
  - worker containers are still not directly host-published

#### Least privilege container hardening

The repository aims to enforce:

- `cap_drop: ALL` by default
- `no-new-privileges`
- selective `read_only: true`
- restricted writable paths and `tmpfs` for transient state
- no `privileged`, host networking, or Docker socket mounts

#### Active isolation monitoring

Containers use [`security_helpers/active_isolation_monitoring_entrypoint.sh`](../security_helpers/active_isolation_monitoring_entrypoint.sh) as a supervisor.

Current behavior:

- probes forbidden ICMP targets
- probes forbidden HTTPS/TCP reachability
- fails closed on monitor audit errors
- terminates the application container if unexpected reachability is detected

This is useful as a last-resort detector, but it is not a replacement for host firewalling.

#### IPv6 safety net

If `ip6tables` is available, the firewall helper applies a default-deny forwarding safety net for the Docker bridge interfaces. If `ip6tables` is unavailable, deployers must disable IPv6 or firewall it separately.

## 4. Residual Risks and Non-Goals

This repository does not claim to solve:

- host compromise
- physical access attacks
- all covert channels
- compromise of an explicitly allowed upstream LDAP or API target
- compromise of the client workstation after data is delivered there

Residual risks that still matter even when the controls work as intended:

- any allowed airlock target is a boundary of trust
- secrets injected as environment variables can still be exposed to privileged host users
- local logs, caches, or persistent application state can retain sensitive data if deployers do not manage them properly

## 5. Secrets, Persistence, and Logging

### 5.1 Secrets

Secrets are typically injected at runtime, not committed to the repository.

Important caveats:

- environment-variable secrets can still be visible via `docker inspect`, `/proc`, or privileged host access
- TLS key material may exist in container memory-backed paths during runtime
- secret safety still depends on host access control

### 5.2 Data persistence

Sensitive data can exist in:

- RAM and GPU memory during inference
- OpenWebUI state directories
- local model and framework caches
- Docker logs if application logging is misconfigured

Deployers should define retention, deletion, and log-rotation policies before real-world use.

### 5.3 Logging guidance

- avoid external log shipping by default
- avoid prompt/completion logging
- audit application log levels before processing real sensitive data

## 6. Deployer Responsibilities

Before processing sensitive data, deployers should do all of the following:

1. run their own security review and legal/compliance review
2. review host hardening, patching, access control, and monitoring
3. define data retention and deletion procedures
4. control image and model supply chain updates
5. rerun the managed security test suites after security-relevant changes

Recommended operational controls:

- use internal or offline image mirrors where possible
- pin image digests instead of floating tags
- scan images and dependencies in a controlled environment
- treat model and infrastructure changes as change-controlled events

## 7. Most Important Security Audit Tests

The highest-signal tests in this repository are the ones that directly validate the isolation model, the host firewall, and the fail-safe helpers.

### 7.1 Core isolation tests

- [`tests/isolation/test_host_firewall.py`](../tests/isolation/test_host_firewall.py)
  - Live root-level audit of `iptables` / `ip6tables`
  - Verifies managed `UKBGPT-*` chains, `DOCKER-USER` jump rules, default-drop behavior, and exact pinned TCP allow rules for active airlocks

- [`tests/isolation/test_egress.py`](../tests/isolation/test_egress.py)
  - Verifies containers cannot reach forbidden ICMP or HTTPS targets
  - Includes an explicit exfiltration attempt toward `api.openai.com`

- [`tests/isolation/test_zz_killswitch.py`](../tests/isolation/test_zz_killswitch.py)
  - Destructive, root-required validation of the active monitor
  - Now includes deterministic live kill-switch tests for:
    - ICMP reachability to a controlled bridge-local forbidden target
    - HTTPS/TCP reachability to a forbidden target

### 7.2 Network topology and host exposure tests

- [`tests/isolation/test_network_attachments.py`](../tests/isolation/test_network_attachments.py)
  - Verifies that services are attached only to their expected networks
  - Guards the micro-segmentation model directly

- [`tests/isolation/test_port_bindings.py`](../tests/isolation/test_port_bindings.py)
  - Verifies that only the intended host ports are published
  - Important for ensuring batch-mode listeners remain loopback-only and that unexpected exposure does not appear on the host

### 7.3 Hardening regression tests

- [`tests/isolation/test_container_hardening.py`](../tests/isolation/test_container_hardening.py)
  - Audits runtime settings such as `privileged`, host namespaces, `cap_drop`, `no-new-privileges`, and read-only rootfs expectations

- [`tests/isolation/test_compose_static.py`](../tests/isolation/test_compose_static.py)
  - Static regression guard against dangerous compose settings
  - Helps catch privileged mode, host networking, Docker socket mounts, and LDAP overlay mistakes before the stack even starts

### 7.4 Helper and fail-closed behavior tests

- [`tests/isolation/test_security_helper_scripts.py`](../tests/isolation/test_security_helper_scripts.py)
  - Directly tests the shell helpers
  - Covers:
    - `check_egress.sh`
    - `healthcheck.sh`
    - `active_isolation_monitoring_entrypoint.sh`
    - `nginx_debug_dump.sh`
    - `install_security_helpers.sh`
  - Especially important for fail-closed monitor behavior and helper correctness under error conditions

- [`tests/isolation/test_firewall_script_exit_codes.py`](../tests/isolation/test_firewall_script_exit_codes.py)
  - Unit-style checks for firewall helper semantics
  - Confirms parse behavior, invalid rule rejection, and default-deny behavior when ingress allow ranges are absent

- [`tests/isolation/test_start_utils_worker_images.py`](../tests/isolation/test_start_utils_worker_images.py)
  - Validates fail-fast startup checks for unsafe configurations
  - Includes checks for non-RFC1918 target IPs, missing `ROOT_CA_PATH`, and invalid secret/environment combinations

### 7.5 Airlock and ingress behavior tests

- [`tests/integration/test_ldap_egress.py`](../tests/integration/test_ldap_egress.py)
  - Verifies the LDAP airlock path and internal bind behavior

- [`tests/integration/test_batch_mode.py`](../tests/integration/test_batch_mode.py)
  - Verifies batch-mode exposure and API egress behavior, including TLS verification

- [`tests/integration/test_ingress_acl.py`](../tests/integration/test_ingress_acl.py)
  - Verifies ingress allow-list and default-deny expectations

- [`tests/integration/test_tls_hardening.py`](../tests/integration/test_tls_hardening.py)
  - Verifies TLS configuration and security headers

- [`tests/integration/test_tls_runtime.py`](../tests/integration/test_tls_runtime.py)
  - Verifies runtime TLS behavior such as minimum version enforcement and TLS 1.3 support

## 8. Legal and Usage Disclaimer

### 8.1 Proof of concept only

This repository is a research-oriented proof of concept for self-hosted, isolation-first LLM deployments. It is provided free of charge as an academic and open-source contribution. It is not a commercial product, managed service, certification, or guarantee that a deployment is secure, lawful, or fit for a particular purpose.

### 8.2 Third-party components

This repository mainly orchestrates third-party software such as Docker, Nginx, OpenWebUI, vLLM, Prometheus, Grafana, and Python-based helpers. The authors do not claim ownership of those upstream components and do not represent that upstream or downstream dependencies are free of defects, vulnerabilities, or unsuitable behavior.

### 8.3 No transfer of institutional approval

Any internal review, approval, or acceptance obtained in one institution, network, or jurisdiction is environment-specific and does not automatically transfer elsewhere. Every deployer must perform its own legal, regulatory, privacy, and security assessment before processing real data.

### 8.4 Not a medical device

This repository is not a medical device and is not intended for diagnosis, prevention, monitoring, prediction, prognosis, treatment, or other clinical decision-making. It must not be relied on as the sole basis for patient care decisions. Any clinical use remains the sole responsibility of the deployer and end user, and qualified human review is required.

### 8.5 Regulatory responsibility

Deployers remain solely responsible for determining whether and how this repository may be used in their environment, including under data protection law, professional secrecy obligations, sector-specific rules, procurement rules, and the EU AI Act where applicable. Use of this repository does not by itself make a deployment compliant, approved, or lawful.

### 8.6 No warranty, no guarantee, no support obligation

To the maximum extent permitted by applicable law, this repository and all accompanying code, configuration, scripts, documentation, and materials are provided "as is" and "as available", without any express or implied warranties, representations, or guarantees. This includes, without limitation, no warranty of merchantability, fitness for a particular purpose, non-infringement, accuracy, completeness, availability, security, or regulatory suitability.

Statements in this repository about architecture, isolation, security goals, hardening, or expected behavior describe design intent only. They do not constitute a guarantee of properties (`Beschaffenheitsgarantie`), a service-level commitment, or a contractual assurance.

There is no obligation to provide support, maintenance, updates, compatibility fixes, or security patches.

### 8.7 Limitation of liability

To the maximum extent permitted by applicable law, the authors, contributors, and rights holders shall not be liable for any direct, indirect, incidental, special, exemplary, consequential, or other damages arising out of or in connection with the use of, inability to use, deployment of, or reliance on this repository, including loss of data, loss of confidentiality, service interruption, procurement costs, regulatory consequences, or other financial or operational losses. This applies irrespective of the legal basis of the claim, whether in contract, quasi-contract, tort, or otherwise, unless mandatory law provides otherwise.

Where a complete exclusion of liability is not legally effective, liability shall, to the extent legally permissible, be limited to intent and gross negligence. To the extent legally permissible, liability for simple negligence is excluded.

Nothing in this disclaimer excludes or limits liability where exclusion is prohibited by mandatory law. This includes, in particular, liability for intent, fraudulent concealment, injury to life, body, or health, and any other non-excludable statutory liability.

For free-of-charge provision, the authors intend that any legally permissible limitation of liability be interpreted consistently with the German-law treatment of gratuitous contributions or gratuitous use.

### 8.8 Vulnerability reporting

Please report security issues responsibly. See `SECURITY.md`.
