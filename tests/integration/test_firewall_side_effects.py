from tests.helpers.commands import docker_exec, run
from tests.integration.common import CHATBOT_PROVIDER_MARKS

pytestmark = CHATBOT_PROVIDER_MARKS


def test_firewall_does_not_break_other_containers():
    # Docker-USER rules should not block unrelated containers.
    container = "ukbgpt_side_effect_test_node"
    try:
        res = run(["docker", "run", "-d", "--name", container, "alpine:latest", "sleep", "3600"], shell=False)
        assert res.code == 0, f"Failed to start side-effect container: {res.output}"

        ping = docker_exec(container, ["ping", "-c", "1", "-W", "2", "8.8.8.8"])
        assert ping.code == 0, "Unrelated container lost internet access (iptables too broad)"
    finally:
        run(["docker", "rm", "-f", container], shell=False)
