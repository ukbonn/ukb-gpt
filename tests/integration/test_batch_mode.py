import time

import requests

from tests.helpers.commands import (
    docker_exec,
    docker_nginx_config,
    docker_ps_name,
)
from tests.integration.common import BATCH_CLIENT_MARKS

pytestmark = BATCH_CLIENT_MARKS


def test_frontend_not_running():
    # Frontend should be absent in batch client mode for reduced surface.
    res = docker_ps_name("ukbgpt_frontend")
    assert not res.stdout.strip(), "Frontend container is running in batch client mode"


def test_metrics_not_running_in_batch():
    # Metrics stack should be disabled in batch client mode.
    for name in ["ukbgpt_grafana", "ukbgpt_prometheus", "ukbgpt_ldap_egress"]:
        res = docker_ps_name(name)
        assert not res.stdout.strip(), f"{name} is running in batch client mode"


def test_api_egress_tls_verification():
    # api_egress must enforce TLS verify with corporate CA.
    output = docker_nginx_config("ukbgpt_api_egress")
    assert "proxy_ssl_verify on;" in output, "api_egress is not enforcing upstream TLS verification"
    assert "proxy_ssl_trusted_certificate /etc/nginx/certs/root_ca.crt;" in output, (
        "api_egress does not reference the corporate root CA"
    )
    assert "location /v1/" in output, "api_egress is missing /v1 ingress location"
    assert "rewrite ^/v1/(.*)$ /api/$1 break;" in output, (
        "api_egress is not rewriting /v1/* to /api/* for OpenWebUI compatibility"
    )

    cert_check = docker_exec(
        "ukbgpt_api_egress",
        ["test", "-s", "/etc/nginx/certs/root_ca.crt"],
    )
    assert cert_check.code == 0, "api_egress root CA bundle is missing or empty"


def _models_seen_in_response(response: requests.Response) -> set[str]:
    seen = set()
    payload = response.text or ""
    if "dummy-model" in payload:
        seen.add("dummy-model")
    if "mock-api-model" in payload:
        seen.add("mock-api-model")
    return seen


def _route_attempts(port: int, host: str, method: str, path: str, **request_kwargs) -> set[str]:
    seen: set[str] = set()
    headers = request_kwargs.pop("headers", {})
    headers = {"Connection": "close", "Host": host, **headers}
    url = f"http://127.0.0.1:{port}{path}"

    for _ in range(40):
        try:
            response = requests.request(method, url, headers=headers, timeout=5, **request_kwargs)
        except requests.RequestException:
            time.sleep(0.75)
            continue

        assert response.status_code == 200, (
            f"Unexpected {path} status code {response.status_code}. Body: {response.text[:240]}"
        )
        seen.update(_models_seen_in_response(response))
        if seen == {"dummy-model", "mock-api-model"}:
            break

        time.sleep(0.75)

    return seen


def test_batch_routes_cover_local_and_api_targets(batch_client_stack):
    # All key routes should occasionally use both local worker and api_egress.
    port = batch_client_stack.batch_port
    host = batch_client_stack.server_name

    routes = [
        ("/v1/models", "GET", {} , "models endpoint"),
        (
            "/v1/chat/completions",
            "POST",
            {"json": {"model": "dummy-model", "messages": [{"role": "user", "content": "ping"}]}},
            "chat completions endpoint",
        ),
        (
            "/v1/embeddings",
            "POST",
            {"json": {"model": "dummy-model", "input": ["embedding ping"]}},
            "embeddings endpoint",
        ),
        (
            "/v1/audio/transcriptions",
            "POST",
            {"data": {"model": "dummy-model"}, "files": {"file": ("sample.wav", b"RIFF0000WAVEfmt ", "audio/wav")}},
            "audio transcriptions endpoint",
        ),
    ]

    expected_models = {"dummy-model", "mock-api-model"}

    for path, method, kwargs, route_label in routes:
        seen = _route_attempts(port, host, method, path, **kwargs)
        assert expected_models.issubset(seen), (
            f"{route_label} did not exercise both local and api_egress paths: {seen}"
        )
