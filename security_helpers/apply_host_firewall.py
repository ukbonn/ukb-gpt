#!/usr/bin/env python3
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

# --- Colors & formatting ---
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
GRAY = "\033[0;90m"
NC = "\033[0m"  # No Color

DOCKER_USER_CHAIN = "DOCKER-USER"
INTERNAL_CHAIN = "UKBGPT-INTERNAL"
INGRESS_CHAIN = "UKBGPT-INGRESS"
EGRESS_CHAIN = "UKBGPT-EGRESS"

INTERNAL_NET = "br-internal"
INGRESS_NET = "br-dmz-ingress"
EGRESS_NET = "br-dmz-egress"
TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EgressRule:
    protocol: str
    address: str
    port: int


def _print_header() -> None:
    print(f"{BLUE}=========================================================={NC}")
    print(f"{BLUE}   UKB-GPT: HOST FIREWALL ENFORCEMENT (PYTHON){NC}")
    print(f"{BLUE}=========================================================={NC}")


def _format_cmd(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def _run_capture(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True)


def _run_cmd(argv: list[str], *, required: bool = True) -> bool:
    print(f"{GRAY}[EXEC] {_format_cmd(argv)}{NC}")
    res = _run_capture(argv)
    if res.returncode == 0:
        return True

    print(f"{RED}      >> Command failed (exit {res.returncode}).{NC}")
    if res.stdout.strip():
        print(res.stdout.strip())
    if res.stderr.strip():
        print(res.stderr.strip())
    if required:
        print(f"{RED}      >> Required firewall step failed.{NC}")
    return False


def _has_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _env_enabled(name: str) -> bool:
    raw = os.getenv(name)
    return raw is not None and raw.strip().lower() in TRUE_VALUES


def _egress_targets_configured() -> bool:
    return any(
        (os.getenv(name) or "").strip()
        for name in ("UKBGPT_FIREWALL_EGRESS_RULES", "EGRESS_TARGET_IPS", "EGRESS_TARGET_IP", "LDAP_TARGET_IP")
    )


def _wait_for_interface(iface: str, *, retries: int = 15, required: bool = False) -> bool:
    print(f"{GRAY}      Waiting for interface {iface}... {NC}", end="", flush=True)
    for _ in range(retries):
        if os.path.exists(f"/sys/class/net/{iface}"):
            print(f"{GREEN}Found.{NC}")
            return True
        time.sleep(0.5)

    if required:
        print(f"{RED}Not Found (required).{NC}")
    else:
        print(f"{YELLOW}Not Found.{NC}")
    return False


def _chain_exists(table_cmd: str, chain: str) -> bool:
    res = _run_capture([table_cmd, "-S", chain])
    return res.returncode == 0


def _ensure_chain(table_cmd: str, chain: str, *, allow_create: bool) -> bool:
    if _chain_exists(table_cmd, chain):
        return True

    if not allow_create:
        print(f"{RED}      >> Missing required {table_cmd} {chain} chain.{NC}")
        return False

    if not _run_cmd([table_cmd, "-N", chain], required=True):
        return False

    if not _chain_exists(table_cmd, chain):
        print(f"{RED}      >> Could not verify {table_cmd} {chain} chain creation.{NC}")
        return False

    return True


def _flush_chain(table_cmd: str, chain: str) -> bool:
    return _run_cmd([table_cmd, "-F", chain], required=True)


def _rule_exists(table_cmd: str, chain: str, args: list[str]) -> bool:
    res = _run_capture([table_cmd, "-C", chain, *args])
    return res.returncode == 0


def _append_rule(table_cmd: str, chain: str, args: list[str], *, description: str) -> bool:
    if not _run_cmd([table_cmd, "-A", chain, *args], required=True):
        return False
    if not _rule_exists(table_cmd, chain, args):
        print(f"{RED}      >> Verification failed for rule: {description}{NC}")
        return False
    print(f"{GREEN}      >> {description} applied and verified.{NC}")
    return True


def _remove_rule_if_present(table_cmd: str, chain: str, args: list[str], *, description: str) -> bool:
    while _rule_exists(table_cmd, chain, args):
        if not _run_cmd([table_cmd, "-D", chain, *args], required=True):
            print(f"{RED}      >> Failed to remove rule: {description}{NC}")
            return False
    return True


def _reinstall_rule(
    table_cmd: str,
    chain: str,
    args: list[str],
    *,
    insert_index: int,
    description: str,
) -> bool:
    if not _remove_rule_if_present(table_cmd, chain, args, description=description):
        return False
    if not _run_cmd([table_cmd, "-I", chain, str(insert_index), *args], required=True):
        return False
    if not _rule_exists(table_cmd, chain, args):
        print(f"{RED}      >> Verification failed for rule: {description}{NC}")
        return False
    print(f"{GREEN}      >> {description} applied and verified.{NC}")
    return True


def _parse_legacy_egress_rules() -> list[EgressRule]:
    rules: list[EgressRule] = []
    raw_multi = (os.getenv("EGRESS_TARGET_IPS") or "").strip()
    if raw_multi:
        for item in raw_multi.replace(";", ",").split(","):
            address = item.strip()
            if address:
                rules.append(EgressRule("tcp", address, 443))

    for key, port in (("EGRESS_TARGET_IP", 443), ("LDAP_TARGET_IP", 636)):
        address = (os.getenv(key) or "").strip()
        if address:
            rules.append(EgressRule("tcp", address, port))
    return rules


def _parse_egress_rules() -> list[EgressRule]:
    raw = (os.getenv("UKBGPT_FIREWALL_EGRESS_RULES") or "").strip()
    if not raw:
        raw_rules = _parse_legacy_egress_rules()
    else:
        raw_rules: list[EgressRule] = []
        for item in raw.replace(";", ",").split(","):
            encoded = item.strip()
            if not encoded:
                continue
            parts = encoded.split("|")
            if len(parts) != 3:
                raise ValueError(
                    "UKBGPT_FIREWALL_EGRESS_RULES entries must use protocol|address|port encoding."
                )
            protocol, address, raw_port = parts
            protocol = protocol.strip().lower()
            address = address.strip()
            if protocol != "tcp":
                raise ValueError(f"Unsupported egress protocol in firewall policy: {protocol}")
            try:
                port = int(raw_port.strip())
            except ValueError as exc:
                raise ValueError(f"Invalid firewall egress port: {raw_port}") from exc
            if port < 1 or port > 65535:
                raise ValueError(f"Firewall egress port out of range: {port}")
            raw_rules.append(EgressRule(protocol, address, port))

    deduped: list[EgressRule] = []
    seen: set[EgressRule] = set()
    for rule in raw_rules:
        if rule in seen:
            continue
        seen.add(rule)
        deduped.append(rule)
    return deduped


def _ensure_managed_chains(table_cmd: str, *, allow_create: bool) -> bool:
    for chain in (INTERNAL_CHAIN, INGRESS_CHAIN, EGRESS_CHAIN):
        if not _ensure_chain(table_cmd, chain, allow_create=allow_create):
            return False
    return True


def _populate_internal_chain(table_cmd: str) -> bool:
    if not _flush_chain(table_cmd, INTERNAL_CHAIN):
        return False
    if not _append_rule(
        table_cmd,
        INTERNAL_CHAIN,
        ["-i", INTERNAL_NET, "!", "-o", INTERNAL_NET, "-j", "DROP"],
        description="Internal strict isolation rule",
    ):
        return False
    return _append_rule(
        table_cmd,
        INTERNAL_CHAIN,
        ["-j", "RETURN"],
        description="Internal chain return rule",
    )


def _populate_ingress_chain(table_cmd: str, ingress_ranges: list[str] | None = None) -> bool:
    if not _flush_chain(table_cmd, INGRESS_CHAIN):
        return False

    for ip_range in ingress_ranges or []:
        if not _append_rule(
            table_cmd,
            INGRESS_CHAIN,
            [
                "-i",
                INGRESS_NET,
                "-d",
                ip_range,
                "-m",
                "conntrack",
                "--ctstate",
                "ESTABLISHED,RELATED",
                "-j",
                "ACCEPT",
            ],
            description=f"Ingress stateful allow for {ip_range}",
        ):
            return False

    return _append_rule(
        table_cmd,
        INGRESS_CHAIN,
        ["-i", INGRESS_NET, "-j", "DROP"],
        description="Ingress default drop rule",
    )


def _populate_egress_chain(table_cmd: str, rules: list[EgressRule], *, description_prefix: str) -> bool:
    if not _flush_chain(table_cmd, EGRESS_CHAIN):
        return False

    for rule in rules:
        if not _append_rule(
            table_cmd,
            EGRESS_CHAIN,
            [
                "-i",
                EGRESS_NET,
                "-p",
                rule.protocol,
                "-d",
                rule.address,
                "--dport",
                str(rule.port),
                "-j",
                "ACCEPT",
            ],
            description=f"{description_prefix} egress allow {rule.address}:{rule.port}/{rule.protocol}",
        ):
            return False

    return _append_rule(
        table_cmd,
        EGRESS_CHAIN,
        ["-i", EGRESS_NET, "-j", "DROP"],
        description=f"{description_prefix} egress strict drop rule",
    )


def _install_jump_rules(table_cmd: str, *, include_egress: bool) -> bool:
    jump_specs = [
        (1, INTERNAL_NET, INTERNAL_CHAIN, "Internal jump rule"),
        (2, INGRESS_NET, INGRESS_CHAIN, "Ingress jump rule"),
    ]
    if include_egress:
        jump_specs.append((3, EGRESS_NET, EGRESS_CHAIN, "Egress jump rule"))

    for index, iface, chain, description in jump_specs:
        if not _reinstall_rule(
            table_cmd,
            DOCKER_USER_CHAIN,
            ["-i", iface, "-j", chain],
            insert_index=index,
            description=description,
        ):
            return False

    if not include_egress:
        if not _remove_rule_if_present(
            table_cmd,
            DOCKER_USER_CHAIN,
            ["-i", EGRESS_NET, "-j", EGRESS_CHAIN],
            description="Stale egress jump rule",
        ):
            return False

    return True


def _ensure_forward_hook_v6(interfaces: list[str]) -> bool:
    if _rule_exists("ip6tables", "FORWARD", ["-j", DOCKER_USER_CHAIN]):
        return True

    for index, iface in enumerate(interfaces, start=1):
        if not _reinstall_rule(
            "ip6tables",
            "FORWARD",
            ["-i", iface, "-j", DOCKER_USER_CHAIN],
            insert_index=index,
            description=f"IPv6 DOCKER-USER hook for {iface}",
        ):
            return False

    return True


def enforce_ipv4_rules() -> bool:
    ingress_ranges = [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    ]

    print(f"\n{BLUE}apply_host_firewall.py [1/4] Securing Secure Zone ({INTERNAL_NET})...{NC}")
    if not _ensure_chain("iptables", DOCKER_USER_CHAIN, allow_create=False):
        return False
    if not _ensure_managed_chains("iptables", allow_create=True):
        return False
    if not _wait_for_interface(INTERNAL_NET, required=True):
        return False
    if not _populate_internal_chain("iptables"):
        return False

    print(f"\n{BLUE}apply_host_firewall.py [2/4] Securing Ingress DMZ ({INGRESS_NET})...{NC}")
    if not _wait_for_interface(INGRESS_NET, required=True):
        return False
    if not _populate_ingress_chain("iptables", ingress_ranges):
        return False

    print(f"\n{BLUE}apply_host_firewall.py [3/4] Securing Egress DMZ ({EGRESS_NET})...{NC}")
    egress_iface_present = _wait_for_interface(EGRESS_NET, required=False)
    include_egress = False
    if egress_iface_present:
        egress_expected = _env_enabled("UKBGPT_EXPECT_EGRESS_BRIDGE")
        include_egress = egress_expected or _egress_targets_configured()
        if include_egress:
            try:
                all_rules = _parse_egress_rules()
            except ValueError as exc:
                print(f"{RED}      >> CRITICAL: {exc}{NC}")
                return False
            v4_rules = [rule for rule in all_rules if ":" not in rule.address]
            if not all_rules:
                print(
                    f"{RED}      >> CRITICAL: UKBGPT_FIREWALL_EGRESS_RULES/EGRESS_TARGET_IP/LDAP_TARGET_IP "
                    f"missing for active egress bridge.{NC}"
                )
                if not _populate_egress_chain("iptables", [], description_prefix="IPv4"):
                    return False
                return False
            print(
                f"{GRAY}      Target rule(s): "
                f"{', '.join(f'{rule.protocol}:{rule.address}:{rule.port}' for rule in all_rules)}{NC}"
            )
            if not _populate_egress_chain("iptables", v4_rules, description_prefix="IPv4"):
                return False
        else:
            print(
                f"{YELLOW}      Interface present, but no egress airlock is enabled for this startup. "
                f"Treating {EGRESS_NET} as stale/disabled.{NC}"
            )
            if not _flush_chain("iptables", EGRESS_CHAIN):
                return False
    else:
        print(f"{GRAY}      Interface not found (feature disabled). Skipping.{NC}")
        if not _flush_chain("iptables", EGRESS_CHAIN):
            return False

    return _install_jump_rules("iptables", include_egress=include_egress)


def enforce_ipv6_rules() -> bool:
    print(f"\n{BLUE}apply_host_firewall.py [4/4] Applying IPv6 safety net (ip6tables)...{NC}")

    if not _has_cmd("ip6tables"):
        print(
            f"{YELLOW}      >> ip6tables not found. IPv6 safety net not applied; "
            f"ensure IPv6 is disabled or separately firewalled.{NC}"
        )
        return True

    if not _ensure_chain("ip6tables", DOCKER_USER_CHAIN, allow_create=True):
        return False
    if not _ensure_managed_chains("ip6tables", allow_create=True):
        return False

    ok = True

    if not _wait_for_interface(INTERNAL_NET, required=True):
        ok = False
    elif not _populate_internal_chain("ip6tables"):
        ok = False

    if not _wait_for_interface(INGRESS_NET, required=True):
        ok = False
    elif not _populate_ingress_chain("ip6tables", []):
        ok = False

    include_egress = False
    if _wait_for_interface(EGRESS_NET, required=False):
        egress_expected = _env_enabled("UKBGPT_EXPECT_EGRESS_BRIDGE")
        include_egress = egress_expected or _egress_targets_configured()
        if include_egress:
            try:
                all_rules = _parse_egress_rules()
            except ValueError as exc:
                print(f"{RED}      >> CRITICAL: {exc}{NC}")
                return False
            v6_rules = [rule for rule in all_rules if ":" in rule.address]
            if not _populate_egress_chain("ip6tables", v6_rules, description_prefix="IPv6"):
                ok = False
        else:
            print(
                f"{YELLOW}      Interface present, but no egress airlock is enabled for this startup. "
                f"Treating {EGRESS_NET} as stale/disabled.{NC}"
            )
            if not _flush_chain("ip6tables", EGRESS_CHAIN):
                ok = False
    else:
        if not _flush_chain("ip6tables", EGRESS_CHAIN):
            ok = False

    interfaces = [INTERNAL_NET, INGRESS_NET]
    if include_egress:
        interfaces.append(EGRESS_NET)
    if not _ensure_forward_hook_v6(interfaces):
        return False

    if not _install_jump_rules("ip6tables", include_egress=include_egress):
        return False

    return ok


def main() -> int:
    _print_header()

    if os.geteuid() != 0:
        print(f"{RED}❌ Error: root privileges are required to enforce host firewall rules.{NC}")
        return 1

    ipv4_ok = enforce_ipv4_rules()
    ipv6_ok = enforce_ipv6_rules()

    print(f"\n{BLUE}=========================================================={NC}")
    if ipv4_ok and ipv6_ok:
        print(f"{GREEN}Firewall Enforcement Complete.{NC}")
        return 0

    print(f"{RED}Firewall Enforcement Failed. Startup must abort.{NC}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
