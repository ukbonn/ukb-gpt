import requests

from tests.integration.common import CHATBOT_PROVIDER_MARKS

pytestmark = CHATBOT_PROVIDER_MARKS


def test_mismatched_host_header_not_200(chatbot_provider_stack):
    # Reject requests with mismatched Host header.
    assert chatbot_provider_stack.https_port is not None
    code = None
    try:
        response = requests.get(
            f"https://127.0.0.1:{chatbot_provider_stack.https_port}/",
            headers={"Host": "evil.example"},
            timeout=5,
            verify=False,
        )
        code = response.status_code
    except requests.RequestException:
        pass

    assert code != 200, "Ingress returned 200 for mismatched Host header"
