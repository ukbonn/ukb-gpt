import datetime
import ipaddress
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

import pytest
import urllib3

from tests.helpers.commands import assert_ok, run
from tests.helpers.docker import (
    compose,
    compose_flags,
    compose_flags_with_generated_models,
    dump_compose_logs,
    ensure_container_ip,
    ensure_dmz_egress_network,
    inspect_container,
    wait_for_container_health,
)
from tests.helpers.pki import create_test_pki

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent
LOG_ROOT = TESTS_ROOT / "logs"
LOGGER = logging.getLogger("tests.stack")

CHATBOT_PROVIDER_COMPOSE_FILES = (
    REPO_ROOT / "compose/base.yml",
    REPO_ROOT / "compose/modes/frontend.provider.yml",
    REPO_ROOT / "compose/features/dmz_egress.yml",
    REPO_ROOT / "compose/features/ldap.yml",
    REPO_ROOT / "compose/features/metrics.yml",
)
HELPER_CHATBOT_PROVIDER_COMPOSE_FILES = (
    REPO_ROOT / "compose/base.yml",
    REPO_ROOT / "compose/modes/frontend.provider.yml",
    REPO_ROOT / "compose/features/dmz_egress.yml",
    REPO_ROOT / "compose/features/ldap.yml",
    TESTS_ROOT / "compose.test.helper_frontend.yml",
)
ICMP_MONITOR_HELPER_CHATBOT_PROVIDER_COMPOSE_FILES = (
    REPO_ROOT / "compose/base.yml",
    REPO_ROOT / "compose/modes/frontend.provider.yml",
    REPO_ROOT / "compose/features/dmz_egress.yml",
    REPO_ROOT / "compose/features/ldap.yml",
    TESTS_ROOT / "compose.test.helper_frontend_icmp_monitor.yml",
)
HTTPS_MONITOR_HELPER_CHATBOT_PROVIDER_COMPOSE_FILES = (
    REPO_ROOT / "compose/base.yml",
    REPO_ROOT / "compose/modes/frontend.provider.yml",
    REPO_ROOT / "compose/features/dmz_egress.yml",
    REPO_ROOT / "compose/features/ldap.yml",
    TESTS_ROOT / "compose.test.helper_frontend_https_monitor.yml",
)
BATCH_CLIENT_COMPOSE_FILES = (
    REPO_ROOT / "compose/base.yml",
    REPO_ROOT / "compose/modes/batch.client.yml",
    REPO_ROOT / "compose/features/dmz_egress.yml",
    REPO_ROOT / "compose/features/api_egress.yml",
)
MOCK_LDAP_COMPOSE_FLAGS = compose_flags(TESTS_ROOT / "compose.test.mock_ldap_server.yml")
MOCK_API_COMPOSE_FLAGS = compose_flags(TESTS_ROOT / "compose.test.mock_api_server.yml")


def _compose_flags_for_model_stack(
    compose_files: tuple[Path, ...],
    env: Optional[Dict[str, str]] = None,
) -> list[str]:
    return compose_flags_with_generated_models(REPO_ROOT, *compose_files, env=env)


def _chatbot_provider_compose_flags(env: Optional[Dict[str, str]] = None) -> list[str]:
    return _compose_flags_for_model_stack(CHATBOT_PROVIDER_COMPOSE_FILES, env=env)


def _helper_chatbot_provider_compose_flags(env: Optional[Dict[str, str]] = None) -> list[str]:
    return _compose_flags_for_model_stack(HELPER_CHATBOT_PROVIDER_COMPOSE_FILES, env=env)


def _icmp_monitor_helper_chatbot_provider_compose_flags(
    env: Optional[Dict[str, str]] = None,
) -> list[str]:
    return _compose_flags_for_model_stack(ICMP_MONITOR_HELPER_CHATBOT_PROVIDER_COMPOSE_FILES, env=env)


def _https_monitor_helper_chatbot_provider_compose_flags(
    env: Optional[Dict[str, str]] = None,
) -> list[str]:
    return _compose_flags_for_model_stack(HTTPS_MONITOR_HELPER_CHATBOT_PROVIDER_COMPOSE_FILES, env=env)


def _batch_client_compose_flags(env: Optional[Dict[str, str]] = None) -> list[str]:
    return _compose_flags_for_model_stack(BATCH_CLIENT_COMPOSE_FILES, env=env)


@dataclass(frozen=True)
class StackContext:
    mode: str
    artifacts_dir: Path
    server_name: str
    batch_port: Optional[int]
    env: Dict[str, str]
    log_dir: Path
    http_port: Optional[int] = None
    https_port: Optional[int] = None
    diagnostic_port: Optional[int] = None
    diagnostic_port_end: Optional[int] = None
    ingress_exporter_port: Optional[int] = None


@dataclass(frozen=True)
class ChatbotProviderPorts:
    http_port: int
    https_port: int
    diagnostic_port: int
    diagnostic_port_end: int
    ingress_exporter_port: int


@dataclass
class ManagedStackInstance:
    context: StackContext
    cleanup: Callable[[], None]


def _batch_compose_env() -> Dict[str, str]:
    return {
        "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS": os.environ.get(
            "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS", "https://127.0.0.1:8443"
        ),
        "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS": os.environ.get(
            "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS",
            os.environ.get("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS", "https://127.0.0.1:8443"),
        ),
        "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_SNI": os.environ.get(
            "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_SNI", "127.0.0.1"
        ),
        "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_SNI": os.environ.get(
            "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_SNI",
            os.environ.get("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_SNI", "127.0.0.1"),
        ),
        "ROOT_CA_PATH": os.environ.get("ROOT_CA_PATH", "/dev/null"),
    }


def _mock_api_env(artifacts_dir: Path, mock_api_ip: str) -> Dict[str, str]:
    return {
        "MOCK_API_IP": mock_api_ip,
        "TEST_ARTIFACTS_PATH": str(artifacts_dir),
    }


def _find_free_localhost_port(*, exclude: set[int] | None = None) -> int:
    excluded = exclude or set()
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in excluded:
            return port


def _find_free_localhost_block(size: int, *, start: int = 35000, end: int = 55000) -> tuple[int, int]:
    for candidate in range(start, end - size + 1):
        sockets: list[socket.socket] = []
        try:
            for port in range(candidate, candidate + size):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind(("127.0.0.1", port))
                sockets.append(sock)
            block = (candidate, candidate + size - 1)
        except OSError:
            for sock in sockets:
                sock.close()
            continue
        else:
            for sock in sockets:
                sock.close()
            return block

    raise AssertionError(f"Unable to reserve a free localhost port block of size {size}")


def _allocate_chatbot_provider_ports() -> ChatbotProviderPorts:
    diagnostic_port, diagnostic_port_end = _find_free_localhost_block(8)
    used = set(range(diagnostic_port, diagnostic_port_end + 1))
    http_port = _find_free_localhost_port(exclude=used)
    used.add(http_port)
    https_port = _find_free_localhost_port(exclude=used)
    used.add(https_port)
    ingress_exporter_port = _find_free_localhost_port(exclude=used)
    return ChatbotProviderPorts(
        http_port=http_port,
        https_port=https_port,
        diagnostic_port=diagnostic_port,
        diagnostic_port_end=diagnostic_port_end,
        ingress_exporter_port=ingress_exporter_port,
    )


def _base_env(artifacts_dir: Path) -> Dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "TEST_ARTIFACTS_PATH": str(artifacts_dir),
            "CERTIFICATE_KEY": env.get("CERTIFICATE_KEY", "dummy-key-for-interpolation"),
            "SSL_CERT_PATH": env.get("SSL_CERT_PATH", "/dev/null"),
            "ROOT_CA_PATH": env.get("ROOT_CA_PATH", "/dev/null"),
            "VLLM_ENDPOINT": env.get("VLLM_ENDPOINT", "localhost:5000"),
            "WEBUI_SECRET_KEY": env.get("WEBUI_SECRET_KEY", "test-secret-key"),
            "SERVER_NAME": env.get("SERVER_NAME", "localhost"),
            "BYPASS_ROUTER": env.get("BYPASS_ROUTER", "true"),
            "ENABLE_INTERNAL_METRICS": env.get("ENABLE_INTERNAL_METRICS", "true"),
            "ENABLE_METRICS_FORWARDING": env.get("ENABLE_METRICS_FORWARDING", "true"),
            "ENABLE_LDAP": env.get("ENABLE_LDAP", "true"),
            "ENABLE_CHAT_PURGER": env.get("ENABLE_CHAT_PURGER", "false"),
            "ENABLE_RATE_LIMITING": env.get("ENABLE_RATE_LIMITING", "false"),
            "LDAP_APP_PASSWORD": env.get("LDAP_APP_PASSWORD", "dummy"),
            "LDAP_TARGET_IP": env.get("LDAP_TARGET_IP", "172.20.0.5"),
            "LDAP_TARGET_SNI": env.get("LDAP_TARGET_SNI", "localhost"),
            "BATCH_CLIENT_MODE_ON": env.get("BATCH_CLIENT_MODE_ON", "false"),
            "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS": env.get(
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS", ""
            ),
            "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_IP": env.get(
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_IP", ""
            ),
            "HF_HOME": env.get("HF_HOME", str(artifacts_dir / "mock_hf_cache")),
            "OPENWEBUI_DATA_DIR": env.get(
                "OPENWEBUI_DATA_DIR", str(artifacts_dir / "openwebui-data")
            ),
            "OPENWEBUI_RUNTIME_UID": env.get(
                "OPENWEBUI_RUNTIME_UID", str(os.getuid()) if os.getuid() != 0 else "999"
            ),
            "OPENWEBUI_RUNTIME_GID": env.get(
                "OPENWEBUI_RUNTIME_GID", str(os.getgid()) if os.getuid() != 0 else "999"
            ),
            "IS_INTEGRATION_TEST": "true",
            "MODEL_DEPLOYMENT_CONFIG": env.get(
                "MODEL_DEPLOYMENT_CONFIG", "tests/model_deployments/mock-llm.toml"
            ),
        }
    )

    Path(env["HF_HOME"]).mkdir(parents=True, exist_ok=True)
    Path(env["OPENWEBUI_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    return env


def _with_batch_env(env: Dict[str, str]) -> Dict[str, str]:
    """Fill in required batch env vars only if missing/empty."""
    merged = dict(env)
    defaults = _batch_compose_env()
    for key, value in defaults.items():
        if not merged.get(key):
            merged[key] = value
    return merged


def _docker_ps_all_ids(*, filters: list[str]) -> list[str]:
    cmd = ["docker", "ps", "-aq", *filters]
    res = run(cmd, shell=False)
    if res.code != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _remove_containers(container_ids: list[str]) -> None:
    for container_id in container_ids:
        run(["docker", "rm", "-f", container_id], shell=False)


def _remove_named_container(name: str) -> None:
    run(["docker", "rm", "-f", name], shell=False)


def _project_volume_names(project_name: str) -> list[str]:
    res = run(
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
        ],
        shell=False,
    )
    if res.code != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _remove_volumes(volume_names: list[str]) -> None:
    for volume_name in volume_names:
        run(["docker", "volume", "rm", "-f", volume_name], shell=False)


def _wait_for_no_containers(*, filters: list[str], description: str, timeout_seconds: int = 30) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not _docker_ps_all_ids(filters=filters):
            return
        time.sleep(1)
    raise AssertionError(f"Timed out waiting for {description} containers to be removed")


def _flush_iptables() -> None:
    if os.geteuid() != 0:
        return
    run(["iptables", "-F", "DOCKER-USER"], shell=False)


def _preflight_cleanup(env: Dict[str, str]) -> None:
    # Remove any leftover project containers (running or stopped) for deterministic
    # startup between test sessions.
    _remove_containers(
        _docker_ps_all_ids(filters=["--filter", "label=com.docker.compose.project=ukbgpt"])
    )
    _remove_containers(_docker_ps_all_ids(filters=["--filter", "name=ukbgpt"]))
    _remove_named_container("ukbgpt_mock_api")
    _remove_named_container("ukbgpt_mock_icmp_target")

    compose(_chatbot_provider_compose_flags(env), ["down", "--volumes", "--remove-orphans"], env=env)
    batch_env = _with_batch_env(env)
    compose(_batch_client_compose_flags(batch_env), ["down", "--volumes", "--remove-orphans"], env=batch_env)
    compose(MOCK_LDAP_COMPOSE_FLAGS, ["down", "--volumes", "--remove-orphans"], env=env)
    compose(MOCK_API_COMPOSE_FLAGS, ["down", "--volumes", "--remove-orphans"], env=batch_env)

    # Force-remove project volumes in case a stopped leftover container pinned them.
    _remove_volumes(_project_volume_names("ukbgpt"))

    _wait_for_no_containers(
        filters=["--filter", "label=com.docker.compose.project=ukbgpt"],
        description="ukbgpt project",
    )
    _wait_for_no_containers(
        filters=["--filter", "name=ukbgpt"],
        description="ukbgpt named",
    )
    _wait_for_no_containers(
        filters=["--filter", "name=ukbgpt_mock_api"],
        description="mock API",
    )
    _wait_for_no_containers(
        filters=["--filter", "name=ukbgpt_mock_icmp_target"],
        description="mock ICMP target",
    )

    run(["docker", "network", "rm", "ukbgpt_dmz_egress"], shell=False)


def _log_dir_for(mode: str) -> Path:
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOG_ROOT / f"pytest_{mode}_{timestamp}"


def _start_stack(env: Dict[str, str]) -> None:
    start_py = REPO_ROOT / "start.py"
    verbose = os.getenv("TEST_STACK_VERBOSE", "false").lower() == "true"
    env = {**env, "PYTHONUNBUFFERED": "1"}
    if verbose:
        LOGGER.info("Starting stack via start.py (verbose output enabled)")
    else:
        LOGGER.info("Starting stack via start.py (this can take several minutes)")
    result = run(
        [sys.executable, "-u", str(start_py)],
        shell=False,
        env=env,
        stream=verbose,
    )
    assert_ok(result, "start.py failed to launch the stack")


def _wait_for_container_running(container_name: str, *, max_retries: int = 20) -> bool:
    for _ in range(max_retries):
        res = run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                container_name,
            ],
            shell=False,
        )
        if res.code == 0 and res.stdout.strip() == "true":
            return True
        time.sleep(1)
    return False


def _assert_containers_running(container_names: list[str]) -> None:
    missing = [name for name in container_names if not _wait_for_container_running(name)]
    if missing:
        raise AssertionError(
            "Timed out waiting for helper-stack containers to stay running: "
            + ", ".join(sorted(missing))
        )


def _load_certificate_key(server_key_path: Path) -> str:
    return server_key_path.read_text(encoding="utf-8")


def _prepare_stack_environment(
    tmp_path_factory,
    *,
    mode: str,
    mode_label: str,
    overrides: Dict[str, str],
) -> tuple[Path, Dict[str, str], Path]:
    artifacts_dir = Path(tmp_path_factory.mktemp(f"artifacts_{mode}"))
    env = _base_env(artifacts_dir)
    env.update(overrides)

    LOGGER.info(f"{mode_label} stack: preflight cleanup")
    _preflight_cleanup(env)
    _flush_iptables()

    LOGGER.info(f"{mode_label} stack: generating test PKI")
    pki = create_test_pki(artifacts_dir)
    env.update(
        {
            "CERTIFICATE_KEY": _load_certificate_key(pki["server_key"]),
            "SSL_CERT_PATH": str(pki["fullchain"]),
            "ROOT_CA_PATH": str(pki["root_crt"]),
        }
    )

    return artifacts_dir, env, _log_dir_for(mode)


def _teardown_stack(
    *,
    mode_label: str,
    primary_compose_flags_factory: Callable[[Dict[str, str]], list[str]],
    primary_env: Dict[str, str],
    log_dir: Path,
    primary_log_prefix: str = "",
    extra_log_targets: list[tuple[list[str], str, Dict[str, str]]] | None = None,
    extra_down_targets: list[tuple[list[str], Dict[str, str]]] | None = None,
) -> None:
    LOGGER.info(f"{mode_label} stack: dumping logs and tearing down")
    primary_compose_flags = primary_compose_flags_factory(primary_env)

    dump_compose_logs(
        primary_compose_flags,
        log_dir,
        prefix=primary_log_prefix,
        env=primary_env,
    )

    for compose_flags, prefix, env in extra_log_targets or []:
        dump_compose_logs(compose_flags, log_dir, prefix=prefix, env=env)

    compose(
        primary_compose_flags,
        ["down", "--volumes", "--remove-orphans"],
        env=primary_env,
    )

    for compose_flags, env in extra_down_targets or []:
        compose(
            compose_flags,
            ["down", "--volumes", "--remove-orphans"],
            env=env,
        )

    run(["docker", "network", "rm", "ukbgpt_dmz_egress"], shell=False)
    run(["docker", "rm", "-f", "ukbgpt_side_effect_test_node"], shell=False)
    run(["docker", "rm", "-f", "ukbgpt_mock_icmp_target"], shell=False)
    _wait_for_no_containers(
        filters=["--filter", "label=com.docker.compose.project=ukbgpt"],
        description="ukbgpt project",
    )
    _wait_for_no_containers(
        filters=["--filter", "name=ukbgpt"],
        description="ukbgpt named",
    )
    _wait_for_no_containers(
        filters=["--filter", "name=ukbgpt_mock_api"],
        description="mock API",
    )
    _wait_for_no_containers(
        filters=["--filter", "name=ukbgpt_mock_icmp_target"],
        description="mock ICMP target",
    )


def _start_mock_icmp_target() -> str:
    container_name = "ukbgpt_mock_icmp_target"
    _remove_named_container(container_name)
    result = run(
        ["docker", "run", "-d", "--name", container_name, "--network", "bridge", "alpine:latest", "sleep", "3600"],
        shell=False,
    )
    assert_ok(result, "Failed to start mock ICMP target")

    deadline = time.time() + 15
    while time.time() < deadline:
        data = inspect_container(container_name)
        target_ip = (
            data.get("NetworkSettings", {})
            .get("Networks", {})
            .get("bridge", {})
            .get("IPAddress", "")
            .strip()
        )
        if target_ip:
            return target_ip
        time.sleep(1)

    raise AssertionError("Mock ICMP target did not obtain a bridge IP")


def _launch_standard_chatbot_provider_stack(
    tmp_path_factory,
    *,
    mode: str,
    mode_label: str,
    overrides: Dict[str, str] | None = None,
) -> ManagedStackInstance:
    ports = _allocate_chatbot_provider_ports()
    artifacts_dir, env, log_dir = _prepare_stack_environment(
        tmp_path_factory,
        mode=mode,
        mode_label=mode_label,
        overrides={
            "BATCH_CLIENT_MODE_ON": "false",
            "ENABLE_LDAP": "true",
            "ENABLE_INTERNAL_METRICS": "true",
            "ENABLE_METRICS_FORWARDING": "true",
            "INGRESS_HTTP_BIND_IP": "127.0.0.1",
            "INGRESS_HTTP_PORT": str(ports.http_port),
            "INGRESS_HTTPS_BIND_IP": "127.0.0.1",
            "INGRESS_HTTPS_PORT": str(ports.https_port),
            "INGRESS_METRICS_BIND_IP": "127.0.0.1",
            "INGRESS_METRICS_PORT_START": str(ports.diagnostic_port),
            "INGRESS_METRICS_PORT_END": str(ports.diagnostic_port_end),
            "INGRESS_EXPORTER_BIND_IP": "127.0.0.1",
            "INGRESS_EXPORTER_PORT": str(ports.ingress_exporter_port),
            **(overrides or {}),
        },
    )

    def _cleanup() -> None:
        _teardown_stack(
            mode_label=mode_label,
            primary_compose_flags_factory=_chatbot_provider_compose_flags,
            primary_env=env,
            log_dir=log_dir,
            extra_log_targets=[
                (MOCK_LDAP_COMPOSE_FLAGS, "mock_ldap_", env),
            ],
            extra_down_targets=[
                (MOCK_LDAP_COMPOSE_FLAGS, env),
            ],
        )

    try:
        LOGGER.info("%s stack: starting mock LDAP", mode_label)
        assert_ok(
            compose(MOCK_LDAP_COMPOSE_FLAGS, ["up", "-d"], env=env),
            "Failed to start mock LDAP stack",
        )
        LOGGER.info("%s stack: launching main stack", mode_label)
        _start_stack(env)

        context = StackContext(
            mode="chatbot_provider",
            artifacts_dir=artifacts_dir,
            server_name=env.get("SERVER_NAME", "localhost"),
            batch_port=None,
            env=env,
            log_dir=log_dir,
            http_port=ports.http_port,
            https_port=ports.https_port,
            diagnostic_port=ports.diagnostic_port,
            diagnostic_port_end=ports.diagnostic_port_end,
            ingress_exporter_port=ports.ingress_exporter_port,
        )
        return ManagedStackInstance(context=context, cleanup=_cleanup)
    except Exception:
        _cleanup()
        raise


def _launch_chatbot_provider_stack(tmp_path_factory) -> ManagedStackInstance:
    return _launch_standard_chatbot_provider_stack(
        tmp_path_factory,
        mode="chatbot_provider",
        mode_label="Chatbot provider",
    )


def _launch_rate_limited_chatbot_provider_stack(tmp_path_factory) -> ManagedStackInstance:
    return _launch_standard_chatbot_provider_stack(
        tmp_path_factory,
        mode="chatbot_provider_rate_limiting",
        mode_label="Chatbot provider (rate limiting)",
        overrides={"ENABLE_RATE_LIMITING": "true"},
    )


def _launch_helper_chatbot_provider_stack(tmp_path_factory) -> ManagedStackInstance:
    artifacts_dir, env, log_dir = _prepare_stack_environment(
        tmp_path_factory,
        mode="chatbot_provider_helpers",
        mode_label="Chatbot provider helper",
        overrides={
            "BATCH_CLIENT_MODE_ON": "false",
            "ENABLE_LDAP": "true",
            "ENABLE_INTERNAL_METRICS": "false",
            "ENABLE_METRICS_FORWARDING": "false",
            "UKBGPT_EXTRA_COMPOSE_FILES": "tests/compose.test.helper_frontend.yml",
        },
    )

    def _cleanup() -> None:
        _teardown_stack(
            mode_label="Chatbot provider helper",
            primary_compose_flags_factory=_helper_chatbot_provider_compose_flags,
            primary_env=env,
            log_dir=log_dir,
            extra_log_targets=[
                (MOCK_LDAP_COMPOSE_FLAGS, "mock_ldap_", env),
            ],
            extra_down_targets=[
                (MOCK_LDAP_COMPOSE_FLAGS, env),
            ],
        )

    try:
        LOGGER.info("Chatbot provider helper stack: starting mock LDAP")
        assert_ok(
            compose(MOCK_LDAP_COMPOSE_FLAGS, ["up", "-d"], env=env),
            "Failed to start mock LDAP stack",
        )
        LOGGER.info("Chatbot provider helper stack: launching main stack")
        _start_stack(env)
        _assert_containers_running(
            [
                "ukbgpt_ingress",
                "ukbgpt_frontend",
                "ukbgpt_ldap_egress",
                "ukbgpt_worker_0",
            ]
        )

        context = StackContext(
            mode="chatbot_provider",
            artifacts_dir=artifacts_dir,
            server_name=env.get("SERVER_NAME", "localhost"),
            batch_port=None,
            env=env,
            log_dir=log_dir,
        )
        return ManagedStackInstance(context=context, cleanup=_cleanup)
    except Exception:
        _cleanup()
        raise


def _launch_icmp_monitor_helper_chatbot_provider_stack(tmp_path_factory) -> ManagedStackInstance:
    artifacts_dir, env, log_dir = _prepare_stack_environment(
        tmp_path_factory,
        mode="chatbot_provider_helpers_icmp_monitor",
        mode_label="Chatbot provider helper ICMP monitor",
        overrides={
            "BATCH_CLIENT_MODE_ON": "false",
            "ENABLE_LDAP": "true",
            "ENABLE_INTERNAL_METRICS": "false",
            "ENABLE_METRICS_FORWARDING": "false",
            "UKBGPT_EXTRA_COMPOSE_FILES": "tests/compose.test.helper_frontend_icmp_monitor.yml",
        },
    )
    target_ip = _start_mock_icmp_target()
    env["TEST_FORBIDDEN_ICMP_TARGET"] = target_ip

    def _cleanup() -> None:
        _teardown_stack(
            mode_label="Chatbot provider helper ICMP monitor",
            primary_compose_flags_factory=_icmp_monitor_helper_chatbot_provider_compose_flags,
            primary_env=env,
            log_dir=log_dir,
            extra_log_targets=[
                (MOCK_LDAP_COMPOSE_FLAGS, "mock_ldap_", env),
            ],
            extra_down_targets=[
                (MOCK_LDAP_COMPOSE_FLAGS, env),
            ],
        )

    try:
        LOGGER.info("Chatbot provider helper ICMP monitor stack: starting mock LDAP")
        assert_ok(
            compose(MOCK_LDAP_COMPOSE_FLAGS, ["up", "-d"], env=env),
            "Failed to start mock LDAP stack",
        )
        LOGGER.info("Chatbot provider helper ICMP monitor stack: launching main stack")
        _start_stack(env)
        _assert_containers_running(
            [
                "ukbgpt_ingress",
                "ukbgpt_frontend",
                "ukbgpt_ldap_egress",
                "ukbgpt_worker_0",
            ]
        )

        context = StackContext(
            mode="chatbot_provider",
            artifacts_dir=artifacts_dir,
            server_name=env.get("SERVER_NAME", "localhost"),
            batch_port=None,
            env=env,
            log_dir=log_dir,
        )
        return ManagedStackInstance(context=context, cleanup=_cleanup)
    except Exception:
        _cleanup()
        raise


def _launch_https_monitor_helper_chatbot_provider_stack(tmp_path_factory) -> ManagedStackInstance:
    artifacts_dir, env, log_dir = _prepare_stack_environment(
        tmp_path_factory,
        mode="chatbot_provider_helpers_https_monitor",
        mode_label="Chatbot provider helper HTTPS monitor",
        overrides={
            "BATCH_CLIENT_MODE_ON": "false",
            "ENABLE_LDAP": "true",
            "ENABLE_INTERNAL_METRICS": "false",
            "ENABLE_METRICS_FORWARDING": "false",
            "UKBGPT_EXTRA_COMPOSE_FILES": "tests/compose.test.helper_frontend_https_monitor.yml",
        },
    )

    def _cleanup() -> None:
        _teardown_stack(
            mode_label="Chatbot provider helper HTTPS monitor",
            primary_compose_flags_factory=_https_monitor_helper_chatbot_provider_compose_flags,
            primary_env=env,
            log_dir=log_dir,
            extra_log_targets=[
                (MOCK_LDAP_COMPOSE_FLAGS, "mock_ldap_", env),
            ],
            extra_down_targets=[
                (MOCK_LDAP_COMPOSE_FLAGS, env),
            ],
        )

    try:
        LOGGER.info("Chatbot provider helper HTTPS monitor stack: starting mock LDAP")
        assert_ok(
            compose(MOCK_LDAP_COMPOSE_FLAGS, ["up", "-d"], env=env),
            "Failed to start mock LDAP stack",
        )
        LOGGER.info("Chatbot provider helper HTTPS monitor stack: launching main stack")
        _start_stack(env)
        _assert_containers_running(
            [
                "ukbgpt_ingress",
                "ukbgpt_frontend",
                "ukbgpt_ldap_egress",
                "ukbgpt_worker_0",
            ]
        )

        context = StackContext(
            mode="chatbot_provider",
            artifacts_dir=artifacts_dir,
            server_name=env.get("SERVER_NAME", "localhost"),
            batch_port=None,
            env=env,
            log_dir=log_dir,
        )
        return ManagedStackInstance(context=context, cleanup=_cleanup)
    except Exception:
        _cleanup()
        raise


def _launch_batch_client_stack(tmp_path_factory) -> ManagedStackInstance:
    artifacts_dir, env, log_dir = _prepare_stack_environment(
        tmp_path_factory,
        mode="batch_client",
        mode_label="Batch client",
        overrides={},
    )
    mock_api_ip = None

    try:
        LOGGER.info("Batch client stack: ensuring dmz_egress network")
        dmz_subnet = ensure_dmz_egress_network()
        if not dmz_subnet:
            raise AssertionError("Failed to create dmz_egress network")

        dmz_net = ipaddress.ip_network(dmz_subnet)
        mock_api_ip = str(dmz_net.network_address + 6)

        LOGGER.info("Batch client stack: starting mock API")
        assert_ok(
            compose(
                MOCK_API_COMPOSE_FLAGS,
                ["up", "-d", "--build"],
                env=_mock_api_env(artifacts_dir, mock_api_ip),
            ),
            "Failed to start mock API stack",
        )

        if not ensure_container_ip("ukbgpt_mock_api", "ukbgpt_dmz_egress", mock_api_ip):
            raise AssertionError("Failed to enforce static IP for mock API")
        if not wait_for_container_health("ukbgpt_mock_api"):
            raise AssertionError("Mock API container is unhealthy")

        env.update(
            {
                "BATCH_CLIENT_MODE_ON": "true",
                "ENABLE_API_EGRESS": "true",
                "BATCH_CLIENT_LISTEN_PORT": "30000",
                "BATCH_CLIENT_EGRESS_PORT": "30100",
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS": f"https://{mock_api_ip}:8443",
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_SNI": "localhost",
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_IP": mock_api_ip,
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS": f"https://{mock_api_ip}:8443",
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_SNI": "localhost",
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_IP": mock_api_ip,
                "ENABLE_LDAP": "false",
                "ENABLE_INTERNAL_METRICS": "false",
                "ENABLE_METRICS_FORWARDING": "true",
            }
        )

        LOGGER.info("Batch client stack: launching main stack")
        _start_stack(env)

        context = StackContext(
            mode="batch_client",
            artifacts_dir=artifacts_dir,
            server_name=env.get("SERVER_NAME", "localhost"),
            batch_port=int(env["BATCH_CLIENT_LISTEN_PORT"]),
            env=env,
            log_dir=log_dir,
        )
        def _cleanup() -> None:
            batch_env = _with_batch_env(env)
            extra_log_targets: list[tuple[list[str], str, Dict[str, str]]] = []
            extra_down_targets: list[tuple[list[str], Dict[str, str]]] = []
            if mock_api_ip:
                mock_api_env = _mock_api_env(artifacts_dir, mock_api_ip)
                extra_log_targets.append(
                    (MOCK_API_COMPOSE_FLAGS, "mock_api_", mock_api_env)
                )
                extra_down_targets.append((MOCK_API_COMPOSE_FLAGS, mock_api_env))

            _teardown_stack(
                mode_label="Batch client",
                primary_compose_flags_factory=_batch_client_compose_flags,
                primary_env=batch_env,
                log_dir=log_dir,
                primary_log_prefix="batch_client_",
                extra_log_targets=extra_log_targets,
                extra_down_targets=extra_down_targets,
            )

        return ManagedStackInstance(context=context, cleanup=_cleanup)
    except Exception:
        batch_env = _with_batch_env(env)
        extra_log_targets: list[tuple[list[str], str, Dict[str, str]]] = []
        extra_down_targets: list[tuple[list[str], Dict[str, str]]] = []
        if mock_api_ip:
            mock_api_env = _mock_api_env(artifacts_dir, mock_api_ip)
            extra_log_targets.append(
                (MOCK_API_COMPOSE_FLAGS, "mock_api_", mock_api_env)
            )
            extra_down_targets.append((MOCK_API_COMPOSE_FLAGS, mock_api_env))

        _teardown_stack(
            mode_label="Batch client",
            primary_compose_flags_factory=_batch_client_compose_flags,
            primary_env=batch_env,
            log_dir=log_dir,
            primary_log_prefix="batch_client_",
            extra_log_targets=extra_log_targets,
            extra_down_targets=extra_down_targets,
        )
        raise


class StackRuntimeManager:
    def __init__(self, tmp_path_factory):
        self._tmp_path_factory = tmp_path_factory
        self._active_name: str | None = None
        self._active_instance: ManagedStackInstance | None = None

    def get(self, name: str) -> StackContext:
        if self._active_name == name and self._active_instance is not None:
            return self._active_instance.context

        self.shutdown()
        launchers = {
            "chatbot_provider": _launch_chatbot_provider_stack,
            "rate_limited_chatbot_provider": _launch_rate_limited_chatbot_provider_stack,
            "helper_chatbot_provider": _launch_helper_chatbot_provider_stack,
            "icmp_monitor_helper_chatbot_provider": _launch_icmp_monitor_helper_chatbot_provider_stack,
            "https_monitor_helper_chatbot_provider": _launch_https_monitor_helper_chatbot_provider_stack,
            "batch_client": _launch_batch_client_stack,
        }
        try:
            launcher = launchers[name]
        except KeyError as exc:
            raise AssertionError(f"Unknown stack mode requested: {name}") from exc

        self._active_instance = launcher(self._tmp_path_factory)
        self._active_name = name
        return self._active_instance.context

    def shutdown(self) -> None:
        if self._active_instance is None:
            return

        cleanup = self._active_instance.cleanup
        self._active_instance = None
        self._active_name = None
        cleanup()


def _prefer_batch_client(request) -> bool:
    node = request.node
    has_batch_marker = node.get_closest_marker("batch_client") is not None
    has_chatbot_marker = node.get_closest_marker("chatbot_provider") is not None
    if has_batch_marker and not has_chatbot_marker:
        return True
    if has_chatbot_marker and not has_batch_marker:
        return False

    markexpr = request.config.option.markexpr or ""
    return "batch_client" in markexpr and "chatbot_provider" not in markexpr


@pytest.fixture(scope="session")
def stack_runtime(tmp_path_factory):
    manager = StackRuntimeManager(tmp_path_factory)
    try:
        yield manager
    finally:
        manager.shutdown()


@pytest.fixture
def stack(request, stack_runtime) -> StackContext:
    if _prefer_batch_client(request):
        return stack_runtime.get("batch_client")
    return stack_runtime.get("chatbot_provider")


@pytest.fixture
def helper_stack(request, stack_runtime) -> StackContext:
    if _prefer_batch_client(request):
        return stack_runtime.get("batch_client")
    return stack_runtime.get("helper_chatbot_provider")


@pytest.fixture
def chatbot_provider_stack(stack_runtime) -> StackContext:
    return stack_runtime.get("chatbot_provider")


@pytest.fixture
def rate_limited_chatbot_provider_stack(stack_runtime) -> StackContext:
    return stack_runtime.get("rate_limited_chatbot_provider")


@pytest.fixture
def helper_chatbot_provider_stack(stack_runtime) -> StackContext:
    return stack_runtime.get("helper_chatbot_provider")


@pytest.fixture
def icmp_monitor_helper_chatbot_provider_stack(stack_runtime) -> StackContext:
    return stack_runtime.get("icmp_monitor_helper_chatbot_provider")


@pytest.fixture
def https_monitor_helper_chatbot_provider_stack(stack_runtime) -> StackContext:
    return stack_runtime.get("https_monitor_helper_chatbot_provider")


@pytest.fixture
def batch_client_stack(stack_runtime) -> StackContext:
    return stack_runtime.get("batch_client")

