import os

import pytest

from tests.helpers.commands import docker_exec
from tests.helpers.docker import find_ingress_container, list_project_containers

pytestmark = [
    pytest.mark.isolation,
    pytest.mark.chatbot_provider,
    pytest.mark.batch_client,
]


def test_container_egress_blocked(helper_stack):
    # Verify containers cannot reach public destinations over ICMP or HTTPS.
    _ = helper_stack
    containers = list_project_containers("ukbgpt")
    assert containers, "No project containers found; is the stack running?"

    # ICMP targets include IPv4 and IPv6 public resolvers.
    icmp_targets = [
        "8.8.8.8",
        "1.1.1.1",
        "9.9.9.9",
        "2001:4860:4860::8888",
        "2606:4700:4700::1111",
        "2620:fe::fe",
    ]
    env_icmp_targets = os.getenv("SECURITY_AUDIT_EGRESS_TARGETS", "").strip()
    if env_icmp_targets:
        icmp_targets = [target for target in env_icmp_targets.replace(",", " ").split() if target]

    # HTTPS targets close the ICMP-only blind spot (TCP/TLS reachability).
    https_targets = ["1.1.1.1", "2606:4700:4700::1111"]
    env_https_targets = os.getenv("SECURITY_AUDIT_HTTPS_TARGETS", "").strip()
    if env_https_targets:
        https_targets = [target for target in env_https_targets.replace(",", " ").split() if target]

    failures = []

    for cont in containers:
        for target in icmp_targets:
            res = docker_exec(
                cont,
                ["/usr/local/bin/check_egress.sh", "--verbose", target],
            )
            if res.code != 1:
                failures.append(
                    (
                        f"{cont} ICMP egress check failed for {target} "
                        f"(expected exit 1, got {res.code}): {res.output}"
                    )
                )

        for target in https_targets:
            res = docker_exec(
                cont,
                ["/usr/local/bin/check_egress.sh", "--verbose", "--https", "--port", "443", target],
            )
            if res.code != 1:
                failures.append(
                    (
                        f"{cont} HTTPS egress check failed for {target}:443 "
                        f"(expected exit 1, got {res.code}): {res.output}"
                    )
                )

    assert not failures, "\n".join(failures)


def test_ingress_https_exfiltration_blocked(helper_stack):
    # Verify ingress cannot reach public HTTPS targets or OpenAI API directly.
    _ = helper_stack
    ingress = find_ingress_container("ukbgpt")
    assert ingress, "Ingress container not found; is the stack running?"

    probe_targets = [
        ("api.openai.com", "https://api.openai.com/"),
        ("www.google.com", "https://www.google.com/"),
    ]

    failures = []
    for host, url in probe_targets:
        res = docker_exec(
            ingress,
            [
                "curl",
                "-sS",
                "--connect-timeout",
                "3",
                "--max-time",
                "8",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                url,
            ],
        )
        http_code = res.stdout.strip() or "000"
        connected = res.code == 0 and http_code != "000"
        if connected:
            failures.append(
                f"{ingress} unexpectedly reached {host} (curl_exit={res.code}, http={http_code})"
            )

    payload = '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello world"}]}'
    exfiltration_attempt = docker_exec(
        ingress,
        [
            "curl",
            "-sS",
            "--connect-timeout",
            "3",
            "--max-time",
            "10",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "-H",
            "Authorization: Bearer sk-blocked-test",
            "-H",
            "Content-Type: application/json",
            "-X",
            "POST",
            "https://api.openai.com/v1/chat/completions",
            "--data",
            payload,
        ],
    )
    exfil_http_code = exfiltration_attempt.stdout.strip() or "000"
    exfil_connected = exfiltration_attempt.code == 0 and exfil_http_code != "000"
    if exfil_connected:
        failures.append(
            (
                f"{ingress} unexpectedly reached api.openai.com chat endpoint "
                f"(curl_exit={exfiltration_attempt.code}, http={exfil_http_code})"
            )
        )

    assert not failures, "\n".join(failures)
