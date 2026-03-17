from tests.helpers.commands import assert_ok, retry_until, run
from tests.integration.common import CHATBOT_PROVIDER_MARKS

pytestmark = CHATBOT_PROVIDER_MARKS


def test_ssl_chain_includes_intermediate(chatbot_provider_stack):
    # TLS chain should include intermediate CA for validation.
    assert chatbot_provider_stack.https_port is not None
    root_ca = chatbot_provider_stack.artifacts_dir / "test_root_ca.crt"
    cmd = [
        "openssl",
        "s_client",
        "-connect",
        f"127.0.0.1:{chatbot_provider_stack.https_port}",
        "-servername",
        chatbot_provider_stack.server_name,
        "-CAfile",
        str(root_ca),
    ]
    result = None

    def _ready() -> bool:
        nonlocal result
        result = run(cmd, shell=False, stdin="")
        return result.code == 0 and "depth=1" in result.output

    assert retry_until(_ready, attempts=12, delay_seconds=2), (
        "TLS endpoint did not present the expected certificate chain in time"
    )
    assert result is not None
    assert_ok(result, "SSL audit command failed")

    verify_log = result.output
    assert "depth=1" in verify_log, "Intermediate CA missing in certificate chain (expected depth=1)"
