import pytest

from security_helpers import apply_host_firewall as firewall

pytestmark = [pytest.mark.isolation, pytest.mark.chatbot_provider, pytest.mark.batch_client]


def test_firewall_script_non_root_returns_nonzero(monkeypatch):
    monkeypatch.setattr(firewall.os, "geteuid", lambda: 1000)
    assert firewall.main() == 1


def test_firewall_script_propagates_ipv4_failure(monkeypatch):
    monkeypatch.setattr(firewall.os, "geteuid", lambda: 0)
    monkeypatch.setattr(firewall, "enforce_ipv4_rules", lambda: False)
    monkeypatch.setattr(firewall, "enforce_ipv6_rules", lambda: True)
    assert firewall.main() == 1


def test_firewall_script_ipv6_tool_absent_does_not_fail_core_path(monkeypatch):
    monkeypatch.setattr(firewall.os, "geteuid", lambda: 0)
    monkeypatch.setattr(firewall, "enforce_ipv4_rules", lambda: True)
    monkeypatch.setattr(firewall, "enforce_ipv6_rules", lambda: True)
    assert firewall.main() == 0


def test_parse_structured_egress_rules_deduplicates(monkeypatch):
    monkeypatch.setenv(
        "UKBGPT_FIREWALL_EGRESS_RULES",
        "tcp|10.0.0.10|443,tcp|10.0.0.10|443,tcp|10.0.0.11|8443",
    )
    monkeypatch.setenv("LDAP_TARGET_IP", "10.0.0.12")

    assert firewall._parse_egress_rules() == [
        firewall.EgressRule("tcp", "10.0.0.10", 443),
        firewall.EgressRule("tcp", "10.0.0.11", 8443),
    ]


def test_parse_structured_egress_rules_rejects_invalid_port(monkeypatch):
    monkeypatch.setenv("UKBGPT_FIREWALL_EGRESS_RULES", "tcp|10.0.0.10|70000")

    with pytest.raises(ValueError, match="out of range"):
        firewall._parse_egress_rules()


def test_parse_legacy_egress_rules_preserves_ldap_port(monkeypatch):
    monkeypatch.delenv("UKBGPT_FIREWALL_EGRESS_RULES", raising=False)
    monkeypatch.setenv("EGRESS_TARGET_IPS", "10.0.0.10,10.0.0.11")
    monkeypatch.setenv("EGRESS_TARGET_IP", "10.0.0.10")
    monkeypatch.setenv("LDAP_TARGET_IP", "10.0.0.12")

    assert firewall._parse_egress_rules() == [
        firewall.EgressRule("tcp", "10.0.0.10", 443),
        firewall.EgressRule("tcp", "10.0.0.11", 443),
        firewall.EgressRule("tcp", "10.0.0.12", 636),
    ]


def test_populate_ingress_chain_without_ranges_is_default_deny(monkeypatch):
    appended: list[tuple[str, str, list[str], str]] = []

    monkeypatch.setattr(firewall, "_flush_chain", lambda *_args, **_kwargs: True)

    def _record_append(table_cmd: str, chain: str, args: list[str], *, description: str) -> bool:
        appended.append((table_cmd, chain, args, description))
        return True

    monkeypatch.setattr(firewall, "_append_rule", _record_append)

    assert firewall._populate_ingress_chain("ip6tables", []) is True
    assert appended == [
        (
            "ip6tables",
            firewall.INGRESS_CHAIN,
            ["-i", firewall.INGRESS_NET, "-j", "DROP"],
            "Ingress default drop rule",
        )
    ]
