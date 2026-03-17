import requests

from tests.helpers.commands import retry_until
from tests.integration.common import CHATBOT_PROVIDER_MARKS

pytestmark = CHATBOT_PROVIDER_MARKS


def test_ingress_does_not_expose_backend(chatbot_provider_stack):
    # Ingress must not leak direct backend model listing.
    assert chatbot_provider_stack.https_port is not None
    server_name = chatbot_provider_stack.server_name
    response = None

    def _ready() -> bool:
        nonlocal response
        try:
            response = requests.get(
                f"https://127.0.0.1:{chatbot_provider_stack.https_port}/v1/models",
                headers={"Host": server_name, "Connection": "close"},
                timeout=5,
                verify=False,
            )
        except requests.RequestException:
            return False
        return True

    assert retry_until(_ready, attempts=12, delay_seconds=2), (
        "Ingress did not become reachable on /v1/models in time"
    )
    assert response is not None
    output = response.text
    assert "dummy-vllm" not in output, (
        "Ingress leaked direct access to vLLM backend. Response contained 'dummy-vllm'."
    )


def _request_root(port: int):
    return requests.get(f"http://127.0.0.1:{port}/", timeout=5)


def _http_code(url: str) -> int | None:
    try:
        return requests.get(url, timeout=5).status_code
    except requests.RequestException:
        return None


def _wait_for_http_code(url: str, attempts: int = 10, delay_seconds: int = 2) -> int | None:
    code = None

    def _ready() -> bool:
        nonlocal code
        code = _http_code(url)
        return code is not None

    retry_until(_ready, attempts=attempts, delay_seconds=delay_seconds)
    return code


def test_diagnostic_tunnel_root_behavior(chatbot_provider_stack):
    # Managed chatbot provider stack always forwards backend scrape port; root must be restricted.
    # Wait until the tunnel returns an HTTP response (avoid startup flakiness).
    assert chatbot_provider_stack.diagnostic_port is not None
    root_url = f"http://127.0.0.1:{chatbot_provider_stack.diagnostic_port}/"
    code = _wait_for_http_code(root_url, attempts=12, delay_seconds=2)
    response = _request_root(chatbot_provider_stack.diagnostic_port)
    output = response.text
    assert code == 403, (
        "Diagnostic tunnel root must return 403 when forwarding is enabled; "
        f"got HTTP {code}. Output:\n{output}"
    )
    assert "Security Alert: Diagnostic tunnel restricted to /metrics" in output, (
        "Diagnostic tunnel root response is missing the expected restriction message. "
        f"Output:\n{output}"
    )
