import os
import shutil
import shlex
from urllib.parse import urlparse

import pytest

from tests.helpers.commands import run

pytestmark = [
    pytest.mark.isolation,
    pytest.mark.chatbot_provider,
    pytest.mark.batch_client,
    pytest.mark.requires_root,
]


def _chain_output(table_cmd: str, chain: str) -> str:
    res = run([table_cmd, "-S", chain], shell=False)
    return res.output


def _env_enabled(env: dict[str, str], key: str) -> bool:
    return env.get(key, "false").strip().lower() == "true"


def _has_pinned_tcp_allow_rule(lines: list[str], address: str, port: int) -> bool:
    for line in lines:
        tokens = shlex.split(line)
        if "ACCEPT" not in tokens:
            continue
        if "-i" not in tokens or tokens[tokens.index("-i") + 1] != "br-dmz-egress":
            continue
        if "-p" not in tokens or tokens[tokens.index("-p") + 1] != "tcp":
            continue
        if "--dport" not in tokens or tokens[tokens.index("--dport") + 1] != str(port):
            continue
        if "-d" not in tokens:
            continue
        destination = tokens[tokens.index("-d") + 1]
        if destination == address or destination == f"{address}/32" or destination == f"{address}/128":
            return True
    return False


def test_host_firewall_rules(helper_stack):
    # Host firewall must include required managed chains and pinned allow rules.
    _ = helper_stack
    if os.geteuid() != 0:
        pytest.skip("Host firewall audit requires root")

    docker_user = _chain_output("iptables", "DOCKER-USER")
    internal = _chain_output("iptables", "UKBGPT-INTERNAL")
    ingress = _chain_output("iptables", "UKBGPT-INGRESS")
    egress = _chain_output("iptables", "UKBGPT-EGRESS")

    assert "-A DOCKER-USER -i br-internal -j UKBGPT-INTERNAL" in docker_user, (
        "Missing jump from DOCKER-USER to UKBGPT-INTERNAL"
    )
    assert "-A DOCKER-USER -i br-dmz-ingress -j UKBGPT-INGRESS" in docker_user, (
        "Missing jump from DOCKER-USER to UKBGPT-INGRESS"
    )

    assert "-A UKBGPT-INTERNAL -i br-internal ! -o br-internal -j DROP" in internal, (
        "Missing br-internal DROP rule"
    )
    assert "-A UKBGPT-INTERNAL -j RETURN" in internal, "Missing br-internal RETURN rule"

    has_stateful_conntrack = (
        "ESTABLISHED,RELATED" in ingress or "RELATED,ESTABLISHED" in ingress
    )
    assert (
        "10.0.0.0/8" in ingress
        and "172.16.0.0/12" in ingress
        and "192.168.0.0/16" in ingress
        and has_stateful_conntrack
        and "ACCEPT" in ingress
    ), "Missing stateful intranet allow-list for br-dmz-ingress"
    assert "-A UKBGPT-INGRESS -i br-dmz-ingress -j DROP" in ingress, (
        "Missing br-dmz-ingress DROP rule"
    )

    if os.path.exists("/sys/class/net/br-dmz-egress"):
        assert "-A DOCKER-USER -i br-dmz-egress -j UKBGPT-EGRESS" in docker_user, (
            "Missing jump from DOCKER-USER to UKBGPT-EGRESS"
        )
        assert "-A UKBGPT-EGRESS -i br-dmz-egress -j DROP" in egress, (
            "Missing strict drop rule for br-dmz-egress"
        )
        egress_lines = [line.strip() for line in egress.splitlines() if line.strip()]

        expected_rules = []
        ldap_target_ip = helper_stack.env.get("LDAP_TARGET_IP", "").strip()
        if _env_enabled(helper_stack.env, "ENABLE_LDAP") and ldap_target_ip:
            expected_rules.append((ldap_target_ip, 636))

        if _env_enabled(helper_stack.env, "ENABLE_API_EGRESS") or helper_stack.mode == "batch_client":
            for address_var in (
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS",
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS",
            ):
                raw = helper_stack.env.get(address_var, "").strip()
                if not raw:
                    continue
                parsed = urlparse(raw)
                if parsed.hostname:
                    expected_rules.append((parsed.hostname, parsed.port or 443))

        expected_rules = list(dict.fromkeys(expected_rules))
        assert expected_rules, "Expected at least one egress allow rule when br-dmz-egress is present"

        for address, port in expected_rules:
            assert _has_pinned_tcp_allow_rule(
                egress_lines, address, port
            ), f"Missing pinned TCP allow rule for {address}:{port}"

    # Best-effort IPv6 verification (only if ip6tables is available).
    if shutil.which("ip6tables"):
        docker_user6 = _chain_output("ip6tables", "DOCKER-USER")
        internal6 = _chain_output("ip6tables", "UKBGPT-INTERNAL")
        ingress6 = _chain_output("ip6tables", "UKBGPT-INGRESS")
        egress6 = _chain_output("ip6tables", "UKBGPT-EGRESS")

        if docker_user6.strip():
            assert "-A DOCKER-USER -i br-internal -j UKBGPT-INTERNAL" in docker_user6, (
                "Missing IPv6 jump to UKBGPT-INTERNAL"
            )
            assert "-A DOCKER-USER -i br-dmz-ingress -j UKBGPT-INGRESS" in docker_user6, (
                "Missing IPv6 jump to UKBGPT-INGRESS"
            )
            assert "-A UKBGPT-INTERNAL -i br-internal ! -o br-internal -j DROP" in internal6, (
                "Missing IPv6 br-internal DROP rule"
            )
            assert "-A UKBGPT-INGRESS -i br-dmz-ingress -j DROP" in ingress6, (
                "Missing IPv6 br-dmz-ingress DROP rule"
            )
            if os.path.exists("/sys/class/net/br-dmz-egress"):
                assert "-A UKBGPT-EGRESS -i br-dmz-egress -j DROP" in egress6, (
                    "Missing IPv6 br-dmz-egress DROP rule"
                )

            forward6 = _chain_output("ip6tables", "FORWARD")
            has_global_hook = "-A FORWARD -j DOCKER-USER" in forward6
            if not has_global_hook:
                assert "-A FORWARD -i br-internal -j DOCKER-USER" in forward6, (
                    "Missing IPv6 DOCKER-USER hook for br-internal"
                )
                assert "-A FORWARD -i br-dmz-ingress -j DOCKER-USER" in forward6, (
                    "Missing IPv6 DOCKER-USER hook for br-dmz-ingress"
                )
                if os.path.exists("/sys/class/net/br-dmz-egress"):
                    assert "-A FORWARD -i br-dmz-egress -j DOCKER-USER" in forward6, (
                        "Missing IPv6 DOCKER-USER hook for br-dmz-egress"
                    )
