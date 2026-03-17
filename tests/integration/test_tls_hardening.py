import requests

from tests.helpers.commands import docker_nginx_config, retry_until
from tests.integration.common import CHATBOT_PROVIDER_MARKS

pytestmark = CHATBOT_PROVIDER_MARKS


def test_ingress_tls_config(chatbot_provider_stack):
    # Nginx TLS config must be hardened and match server_name.
    output = docker_nginx_config("ukbgpt_ingress")

    assert "ssl_protocols TLSv1.2 TLSv1.3;" in output, "Ingress TLS protocols not hardened"
    assert "ssl_prefer_server_ciphers on;" in output, "Ingress does not prefer server ciphers"
    assert "ssl_ciphers" in output, "Ingress does not define cipher list"
    assert "Strict-Transport-Security" in output, "HSTS header not configured in ingress"

    server_name = chatbot_provider_stack.server_name
    assert f"server_name {server_name};" in output, "Ingress server_name does not match expected value"


def test_ingress_security_headers_present(chatbot_provider_stack):
    # Security headers should be present on ingress responses.
    assert chatbot_provider_stack.https_port is not None
    server_name = chatbot_provider_stack.server_name
    response_headers = {}

    def _ready() -> bool:
        nonlocal response_headers
        try:
            response = requests.head(
                f"https://127.0.0.1:{chatbot_provider_stack.https_port}/",
                headers={"Host": server_name},
                timeout=5,
                verify=False,
            )
        except requests.RequestException:
            return False

        response_headers = response.headers
        return True

    assert retry_until(_ready, attempts=10, delay_seconds=2), "Ingress did not return headers in time"
    header_names = {name.lower() for name in response_headers}

    assert "strict-transport-security" in header_names, "HSTS header missing from ingress response"
    assert "content-security-policy" in header_names, "CSP header missing from ingress response"
    assert "x-content-type-options" in header_names, "X-Content-Type-Options header missing"
