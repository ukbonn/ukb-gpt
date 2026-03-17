import pytest

from tests.helpers.commands import docker_nginx_config


@pytest.mark.integration
@pytest.mark.chatbot_provider
@pytest.mark.usefixtures("chatbot_provider_stack")
def test_ingress_acl_standard():
    # Standard ingress ACL should default-deny and allow intranet/localhost.
    output = docker_nginx_config("ukbgpt_ingress")

    assert "deny all;" in output, "Ingress ACL does not deny by default"
    assert "allow 10.0.0.0/8;" in output, "Ingress ACL missing 10.0.0.0/8 allow"
    assert "allow 127.0.0.1;" in output, "Ingress ACL missing localhost allow"


@pytest.mark.integration
@pytest.mark.batch_client
@pytest.mark.usefixtures("batch_client_stack")
def test_ingress_acl_batch():
    # Batch ingress ACL should default-deny and allow local/docker internal.
    output = docker_nginx_config("ukbgpt_ingress")

    assert "deny all;" in output, "Batch ingress ACL does not deny by default"
    assert "allow 127.0.0.1;" in output, "Batch ingress ACL missing localhost allow"
    assert "allow 172.16.0.0/12;" in output, "Batch ingress ACL missing docker internal allow"
