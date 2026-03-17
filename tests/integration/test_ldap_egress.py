import pytest

from tests.helpers.commands import docker_exec, docker_nginx_config
from tests.helpers.docker import list_project_containers
from tests.helpers.network import connection_test

pytestmark = [pytest.mark.integration, pytest.mark.chatbot_provider]


def test_ldap_egress_paths(helper_chatbot_provider_stack):
    # Only approved LDAP path should work; direct/bypass should fail.
    ldap_target_ip = helper_chatbot_provider_stack.env.get("LDAP_TARGET_IP", "172.20.0.5")

    # Whitelisted path: frontend -> ldap proxy
    res = connection_test("ukbgpt_frontend", "ukbgpt_ldap_egress", 389)
    output = res.output
    assert res.code == 0 and "CONNECTED" in output and "REJECTED" not in output, (
        "Frontend did not establish an allowed LDAP proxy connection. Output:\n" + output
    )

    workers = [
        name
        for name in list_project_containers("ukbgpt")
        if (
            name.startswith("ukbgpt_worker_")
            or name.startswith("ukbgpt_embedding_worker_")
            or name.startswith("ukbgpt_stt_worker_")
        )
    ]
    assert workers, "No backend worker container found for LDAP egress isolation checks"
    source_worker = sorted(workers)[0]

    # Unauthorized path: worker -> ldap proxy (should be rejected)
    res = connection_test(source_worker, "ukbgpt_ldap_egress", 389)
    output = res.output
    assert res.code == 3 and "REJECTED" in output, (
        "Worker access to LDAP proxy was not rejected. Output:\n" + output
    )

    # Direct bypass: worker -> LDAP target IP should fail
    egress_res = docker_exec(
        source_worker,
        ["/usr/local/bin/check_egress.sh", "--verbose", ldap_target_ip],
    )
    assert egress_res.code == 1, (
        "Worker reached LDAP target IP directly or bypass check failed. "
        f"Exit={egress_res.code}, Output:\n{egress_res.output}"
    )


def test_ldap_egress_binds_internal_interface_only(helper_chatbot_provider_stack):
    _ = helper_chatbot_provider_stack
    config = docker_nginx_config("ukbgpt_ldap_egress")

    assert "listen 172.16.238.13:389;" in config, (
        "ldap_egress should bind only on its docker_internal address"
    )
