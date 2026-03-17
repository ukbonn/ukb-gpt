from tests.helpers.commands import run
from tests.integration.common import CHATBOT_PROVIDER_MARKS

pytestmark = CHATBOT_PROVIDER_MARKS


def _openssl_s_client(port: int, server_name: str, args: str) -> str:
    cmd = [
        "openssl",
        "s_client",
        "-connect",
        f"127.0.0.1:{port}",
        "-servername",
        server_name,
        args,
    ]
    return run(cmd, shell=False, stdin="").output


def _assert_tls_version(output: str, version: str) -> None:
    markers = [
        f"Protocol  : {version}",
        f"Protocol: {version}",
        f"New, {version}",
    ]
    if not any(marker in output for marker in markers):
        raise AssertionError(f"Ingress did not negotiate {version}")


def test_tls_min_version_enforced(chatbot_provider_stack):
    # Enforce TLS >= 1.2 and reject TLS 1.1.
    assert chatbot_provider_stack.https_port is not None
    output_tls11 = _openssl_s_client(
        chatbot_provider_stack.https_port,
        chatbot_provider_stack.server_name,
        "-tls1_1",
    )
    assert "Protocol  : TLSv1.1" not in output_tls11 and "Protocol: TLSv1.1" not in output_tls11 and "New, TLSv1.1" not in output_tls11, (
        "Ingress accepted TLS 1.1; expected rejection"
    )

    output_tls12 = _openssl_s_client(
        chatbot_provider_stack.https_port,
        chatbot_provider_stack.server_name,
        "-tls1_2",
    )
    _assert_tls_version(output_tls12, "TLSv1.2")


def test_tls13_supported(chatbot_provider_stack):
    # Ensure TLS 1.3 negotiation works.
    assert chatbot_provider_stack.https_port is not None
    output_tls13 = _openssl_s_client(
        chatbot_provider_stack.https_port,
        chatbot_provider_stack.server_name,
        "-tls1_3",
    )
    _assert_tls_version(output_tls13, "TLSv1.3")
