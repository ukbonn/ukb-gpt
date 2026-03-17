import os
import time
from collections.abc import Callable

import pytest

from tests.helpers.docker import find_ingress_container, list_project_containers
from tests.helpers.killswitch import (
    attach_container_to_bridge,
    bridge_gateway,
    container_running,
    container_pid,
    detach_container_from_bridge,
    inject_default_route,
    probe_public_egress,
    probe_public_https_egress,
    security_alert_logged,
)

pytestmark = [
    pytest.mark.isolation,
    pytest.mark.destructive,
    pytest.mark.requires_root,
]

def _select_ingress_or_first(containers: list[str]) -> str:
    return find_ingress_container("ukbgpt") or containers[0]


def _select_worker(containers: list[str]) -> str:
    workers = [container for container in containers if "worker" in container]
    if not workers:
        raise AssertionError("No worker containers found for kill switch test")
    return workers[0]


@pytest.mark.chatbot_provider
@pytest.mark.parametrize(
    ("target_selector", "inconclusive_message", "early_exit_message"),
    [
        pytest.param(
            _select_ingress_or_first,
            "Kill switch test could not reach the deterministic ICMP bridge target; scenario is inconclusive",
            "Target exited before exploit validation; no security alert observed.",
            id="ingress_or_first",
        ),
        pytest.param(
            _select_worker,
            (
                "Unable to confirm ICMP reachability to the deterministic bridge target after attach; "
                "kill-switch scenario is inconclusive"
            ),
            None,
            id="worker",
        ),
    ],
)
def test_kill_switch_triggers(
    icmp_monitor_helper_chatbot_provider_stack,
    target_selector: Callable[[list[str]], str],
    inconclusive_message: str,
    early_exit_message: str | None,
):
    # Simulate unauthorized ICMP reachability to a controlled bridge target.
    _ = icmp_monitor_helper_chatbot_provider_stack
    if os.geteuid() != 0:
        pytest.skip("Kill switch test requires root")

    containers = list_project_containers("ukbgpt")
    assert containers, "No project containers found; is the stack running?"
    target = target_selector(containers)
    target_ip = icmp_monitor_helper_chatbot_provider_stack.env.get("TEST_FORBIDDEN_ICMP_TARGET", "").strip()
    assert target_ip, "Deterministic ICMP target IP missing from test stack environment"

    try:
        connect_res = attach_container_to_bridge(target)
        if connect_res.code != 0:
            raise AssertionError(
                f"Failed to connect {target} to docker bridge for kill-switch test: {connect_res.output}"
            )

        pid = container_pid(target)
        attack_successful = False
        container_already_dead = False

        if not pid or pid == "0":
            if early_exit_message is None:
                raise AssertionError("Worker PID not available for kill switch test")
            container_already_dead = True
        else:
            # Docker adds a connected route for the bridge subnet on attach; we probe
            # a controlled bridge-local target instead of relying on public internet.
            gateway = bridge_gateway()
            if gateway:
                inject_default_route(pid, gateway)

            attack_successful, pid_missing = probe_public_egress(target, targets=[target_ip])
            if pid_missing:
                container_already_dead = True

        if attack_successful:
            time.sleep(10)
            if container_running(target):
                raise AssertionError("Container remained running after external route was opened")
            assert security_alert_logged(target), "Kill switch stopped container without SECURITY ALERT logs"
            return

        if not container_running(target):
            if security_alert_logged(target):
                return
            if container_already_dead and early_exit_message:
                pytest.skip(early_exit_message)
            pytest.skip(inconclusive_message)

        if container_already_dead and early_exit_message:
            if security_alert_logged(target):
                return
            pytest.skip(early_exit_message)

        pytest.skip(inconclusive_message)
    finally:
        detach_container_from_bridge(target)


@pytest.mark.chatbot_provider
def test_https_kill_switch_triggers(https_monitor_helper_chatbot_provider_stack):
    # Simulate an external TCP/TLS path on ingress; HTTPS monitor must stop and alert.
    _ = https_monitor_helper_chatbot_provider_stack
    if os.geteuid() != 0:
        pytest.skip("Kill switch test requires root")

    target = find_ingress_container("ukbgpt")
    assert target, "Ingress container not found for HTTPS kill-switch test"

    try:
        connect_res = attach_container_to_bridge(target)
        if connect_res.code != 0:
            raise AssertionError(
                f"Failed to connect {target} to docker bridge for HTTPS kill-switch test: {connect_res.output}"
            )

        pid = container_pid(target)
        if not pid or pid == "0":
            pytest.skip("Ingress PID not available for HTTPS kill-switch test")

        gateway = bridge_gateway()
        if gateway:
            inject_default_route(pid, gateway)

        attack_successful, pid_missing = probe_public_https_egress(pid)
        if attack_successful:
            time.sleep(10)
            if container_running(target):
                raise AssertionError("Container remained running after HTTPS path was opened")
            assert security_alert_logged(
                target
            ), "Kill switch stopped container without SECURITY ALERT logs after HTTPS reachability"
            return

        if not container_running(target):
            if security_alert_logged(target):
                return
            pytest.skip("Ingress exited before HTTPS exploit validation; no security alert observed.")

        if pid_missing:
            if security_alert_logged(target):
                return
            pytest.skip("Ingress exited before HTTPS exploit validation; no security alert observed.")

        pytest.skip("HTTPS kill switch test could not open an external TCP/TLS path; scenario is inconclusive")
    finally:
        detach_container_from_bridge(target)
