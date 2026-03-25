import uuid

import pytest
import requests
import urllib3

from tests.helpers.commands import retry_until, run


pytestmark = [pytest.mark.integration, pytest.mark.chatbot_provider]

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PIPELINE_ID = "adaptive_rate_limit_filter_pipeline"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
API_USER_AGENT = "curl/8.7.1"


def _url(stack, path: str) -> str:
    assert stack.https_port is not None
    return f"https://127.0.0.1:{stack.https_port}{path}"


def _headers(
    stack,
    token: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "Host": stack.server_name,
        "Connection": "close",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    return headers


def _request(
    stack,
    method: str,
    path: str,
    token: str | None = None,
    extra_headers: dict[str, str] | None = None,
    **kwargs,
):
    return requests.request(
        method,
        _url(stack, path),
        headers=_headers(stack, token, extra_headers),
        timeout=10,
        verify=False,
        **kwargs,
    )


def _wait_for_frontend_https(stack) -> None:
    def _ready() -> bool:
        try:
            response = _request(stack, "GET", "/health")
        except requests.RequestException:
            return False
        return response.status_code == 200

    assert retry_until(_ready, attempts=20, delay_seconds=2), (
        "Frontend did not become ready on ingress /health in time"
    )


def _create_or_login_admin(stack, email: str, password: str) -> dict:
    payload = {
        "name": "Pipeline Admin",
        "email": email,
        "password": password,
    }

    signup = _request(stack, "POST", "/api/v1/auths/signup", json=payload)
    if signup.status_code == 200:
        return signup.json()

    signin = _request(
        stack,
        "POST",
        "/api/v1/auths/signin",
        json={"email": email, "password": password},
    )
    assert signin.status_code == 200, signin.text
    return signin.json()


def _add_user(stack, admin_token: str, email: str, password: str) -> dict:
    response = _request(
        stack,
        "POST",
        "/api/v1/auths/add",
        token=admin_token,
        json={
            "name": email.split("@", 1)[0],
            "email": email,
            "password": password,
            "role": "user",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_pipelines_url_idx(stack, admin_token: str) -> int:
    result = {"idx": None}

    def _ready() -> bool:
        response = _request(stack, "GET", "/api/v1/pipelines/list", token=admin_token)
        if response.status_code != 200:
            return False
        for item in response.json().get("data", []):
            if item.get("url") == "http://pipelines:9099/v1":
                result["idx"] = item["idx"]
                return True
        return False

    assert retry_until(_ready, attempts=20, delay_seconds=2), (
        "OpenWebUI did not detect the internal pipelines service in time"
    )
    assert result["idx"] is not None
    return int(result["idx"])


def _get_pipelines(stack, admin_token: str, url_idx: int) -> list[dict]:
    response = _request(
        stack,
        "GET",
        f"/api/v1/pipelines/?urlIdx={url_idx}",
        token=admin_token,
    )
    assert response.status_code == 200, response.text
    return response.json().get("data", [])


def _get_pipeline_valves(stack, admin_token: str, url_idx: int) -> dict:
    response = _request(
        stack,
        "GET",
        f"/api/v1/pipelines/{PIPELINE_ID}/valves?urlIdx={url_idx}",
        token=admin_token,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _update_pipeline_valves(
    stack,
    admin_token: str,
    url_idx: int,
    valves: dict,
) -> dict:
    response = _request(
        stack,
        "POST",
        f"/api/v1/pipelines/{PIPELINE_ID}/valves/update?urlIdx={url_idx}",
        token=admin_token,
        json=valves,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _get_models(stack, token: str) -> list[dict]:
    result = {"models": None}

    def _ready() -> bool:
        response = _request(stack, "GET", "/api/models", token=token)
        if response.status_code != 200:
            return False
        models = response.json().get("data", [])
        if not models:
            return False
        result["models"] = models
        return True

    assert retry_until(_ready, attempts=20, delay_seconds=2), (
        "OpenWebUI did not return a model list in time"
    )
    assert result["models"] is not None
    return result["models"]


def _get_model_id(stack, token: str) -> str:
    return _get_models(stack, token)[0]["id"]


def _chat_completion(
    stack,
    token: str,
    model_id: str,
    *,
    extra_headers: dict[str, str] | None = None,
):
    return _request(
        stack,
        "POST",
        "/api/chat/completions",
        token=token,
        extra_headers=extra_headers,
        json={
            "model": model_id,
            "stream": False,
            "messages": [{"role": "user", "content": "Ping"}],
        },
    )


def _container_logs(container_name: str) -> str:
    result = run(["docker", "logs", container_name], shell=False)
    return f"{result.stdout}{result.stderr}"


def test_chatbot_provider_without_rate_limiting_uses_direct_backend(
    chatbot_provider_stack,
):
    admin_email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    admin_password = "TestPassword123!"
    _wait_for_frontend_https(chatbot_provider_stack)
    admin = _create_or_login_admin(
        chatbot_provider_stack, admin_email, admin_password
    )
    admin_token = admin["token"]

    response = _request(
        chatbot_provider_stack,
        "GET",
        "/api/v1/pipelines/list",
        token=admin_token,
    )
    assert response.status_code == 200, response.text
    assert not any(
        item.get("url") == "http://pipelines:9099/v1"
        for item in response.json().get("data", [])
    ), response.text

    model_id = _get_model_id(chatbot_provider_stack, admin_token)
    chat = _chat_completion(chatbot_provider_stack, admin_token, model_id)
    assert chat.status_code == 200, chat.text


def test_adaptive_rate_limit_pipeline_switches_between_static_and_adaptive_modes(
    rate_limited_chatbot_provider_stack,
):
    admin_email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    admin_password = "TestPassword123!"
    _wait_for_frontend_https(rate_limited_chatbot_provider_stack)
    admin = _create_or_login_admin(
        rate_limited_chatbot_provider_stack, admin_email, admin_password
    )
    admin_token = admin["token"]

    url_idx = _wait_for_pipelines_url_idx(
        rate_limited_chatbot_provider_stack,
        admin_token,
    )
    pipelines = _get_pipelines(
        rate_limited_chatbot_provider_stack,
        admin_token,
        url_idx,
    )
    assert any(item.get("id") == PIPELINE_ID for item in pipelines), pipelines

    original_valves = _get_pipeline_valves(
        rate_limited_chatbot_provider_stack,
        admin_token,
        url_idx,
    )

    model_id = _get_model_id(rate_limited_chatbot_provider_stack, admin_token)
    static_user = _add_user(
        rate_limited_chatbot_provider_stack,
        admin_token,
        f"static-{uuid.uuid4().hex[:8]}@example.com",
        "TestPassword123!",
    )
    adaptive_user = _add_user(
        rate_limited_chatbot_provider_stack,
        admin_token,
        f"adaptive-{uuid.uuid4().hex[:8]}@example.com",
        "TestPassword123!",
    )

    try:
        static_valves = {
            **original_valves,
            "mode": "static",
            "requests_per_minute": 1,
            "requests_per_hour": 10,
            "sliding_window_limit": 10,
            "sliding_window_minutes": 15,
            "priority_whitelist": "",
            "rate_limit_whitelist": "",
            "allow_anonymous_requests": False,
        }
        updated_static = _update_pipeline_valves(
            rate_limited_chatbot_provider_stack,
            admin_token,
            url_idx,
            static_valves,
        )
        assert updated_static["mode"] == "static"

        first_static = _chat_completion(
            rate_limited_chatbot_provider_stack,
            static_user["token"],
            model_id,
        )
        assert first_static.status_code == 200, first_static.text

        second_static = _chat_completion(
            rate_limited_chatbot_provider_stack,
            static_user["token"],
            model_id,
        )
        assert second_static.status_code == 429, second_static.text
        assert "Limit 1/min exceeded" in second_static.json().get("detail", "")

        adaptive_valves = {
            **original_valves,
            "mode": "adaptive",
            "metrics_url": "http://worker_0:5000/metrics",
            "day_rate_limit": '{"0": 1}',
            "night_rate_limit": '{"0": 1}',
            "fallback_day_rate_limit": 1,
            "fallback_night_rate_limit": 1,
            "update_adaptive_rate_limits_interval_seconds": 1,
            "priority_whitelist": "",
            "rate_limit_whitelist": "",
            "allow_anonymous_requests": False,
        }
        updated_adaptive = _update_pipeline_valves(
            rate_limited_chatbot_provider_stack,
            admin_token,
            url_idx,
            adaptive_valves,
        )
        assert updated_adaptive["mode"] == "adaptive"

        first_adaptive = _chat_completion(
            rate_limited_chatbot_provider_stack,
            adaptive_user["token"],
            model_id,
        )
        assert first_adaptive.status_code == 200, first_adaptive.text

        second_adaptive = _chat_completion(
            rate_limited_chatbot_provider_stack,
            adaptive_user["token"],
            model_id,
        )
        assert second_adaptive.status_code == 429, second_adaptive.text
        assert "Limit 1/min exceeded" in second_adaptive.json().get("detail", "")
    finally:
        _update_pipeline_valves(
            rate_limited_chatbot_provider_stack,
            admin_token,
            url_idx,
            original_valves,
        )


def test_adaptive_rate_limit_pipeline_forwards_browser_request_metadata(
    rate_limited_chatbot_provider_stack,
):
    admin_email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    admin_password = "TestPassword123!"
    _wait_for_frontend_https(rate_limited_chatbot_provider_stack)
    admin = _create_or_login_admin(
        rate_limited_chatbot_provider_stack, admin_email, admin_password
    )
    admin_token = admin["token"]

    url_idx = _wait_for_pipelines_url_idx(
        rate_limited_chatbot_provider_stack,
        admin_token,
    )
    original_valves = _get_pipeline_valves(
        rate_limited_chatbot_provider_stack,
        admin_token,
        url_idx,
    )
    model_id = _get_model_id(rate_limited_chatbot_provider_stack, admin_token)
    browser_user = _add_user(
        rate_limited_chatbot_provider_stack,
        admin_token,
        f"browser-{uuid.uuid4().hex[:8]}@example.com",
        "TestPassword123!",
    )
    api_user = _add_user(
        rate_limited_chatbot_provider_stack,
        admin_token,
        f"api-{uuid.uuid4().hex[:8]}@example.com",
        "TestPassword123!",
    )

    try:
        updated_valves = _update_pipeline_valves(
            rate_limited_chatbot_provider_stack,
            admin_token,
            url_idx,
            {
                **original_valves,
                "mode": "static",
                "requests_per_minute": 10,
                "requests_per_hour": 100,
                "sliding_window_limit": 100,
                "sliding_window_minutes": 15,
                "priority_whitelist": "",
                "rate_limit_whitelist": "",
                "inject_priority": True,
                "allow_anonymous_requests": False,
                "enable_debug_logging": True,
            },
        )
        assert updated_valves["inject_priority"] is True

        browser_response = _chat_completion(
            rate_limited_chatbot_provider_stack,
            browser_user["token"],
            model_id,
            extra_headers={"User-Agent": BROWSER_USER_AGENT},
        )
        assert browser_response.status_code == 200, browser_response.text

        api_response = _chat_completion(
            rate_limited_chatbot_provider_stack,
            api_user["token"],
            model_id,
            extra_headers={"User-Agent": API_USER_AGENT},
        )
        assert api_response.status_code == 200, api_response.text

        expected_browser = (
            f"Priority decision ident={browser_user['email']} request_priority=0"
        )
        expected_api = f"Priority decision ident={api_user['email']} request_priority=1"

        assert retry_until(
            lambda: expected_browser in _container_logs("ukbgpt_pipelines")
            and expected_api in _container_logs("ukbgpt_pipelines"),
            attempts=15,
            delay_seconds=2,
        ), _container_logs("ukbgpt_pipelines")
    finally:
        _update_pipeline_valves(
            rate_limited_chatbot_provider_stack,
            admin_token,
            url_idx,
            original_valves,
        )
