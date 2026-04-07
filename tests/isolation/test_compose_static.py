import os
from pathlib import Path

import pytest
from utils.models import deployment as model_deployment
from utils.stack import schema as env_schema

pytestmark = [
    pytest.mark.isolation,
    pytest.mark.chatbot_provider,
    pytest.mark.batch_client,
]

ROOT = Path(__file__).resolve().parents[2]

COMPOSE_FILES = [
    ROOT / "compose/hardening.yml",
    ROOT / "compose/base.yml",
    ROOT / "compose/modes/frontend.provider.yml",
    ROOT / "compose/modes/batch.client.yml",
    ROOT / "compose/apps/dataset_structuring.yml",
    ROOT / "compose/apps/cohort_feasibility.yml",
    ROOT / "compose/apps/dictation.yml",
    ROOT / "compose/features/dmz_egress.yml",
    ROOT / "compose/features/api_egress.yml",
    ROOT / "compose/features/ldap.yml",
    ROOT / "compose/features/metrics.yml",
    ROOT / "compose/features/chat_purger.yml",
] + sorted((ROOT / "compose/models").rglob("*.yml"))


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError:
        raise RuntimeError(
            "PyYAML is required for static compose checks. Install with: python3 -m pip install pyyaml"
        )
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _get_nested(data: dict, keys: list[str], default=None):
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _check_service(name: str, svc: dict, failures: list) -> None:
    if svc.get("privileged") is True:
        failures.append(f"{name}: privileged=true is not allowed")
    if svc.get("network_mode") == "host":
        failures.append(f"{name}: network_mode=host is not allowed")
    if svc.get("pid") == "host":
        failures.append(f"{name}: pid=host is not allowed")
    if svc.get("ipc") == "host":
        failures.append(f"{name}: ipc=host is not allowed")

    cap_add = svc.get("cap_add") or []
    if cap_add:
        allowed = {"NET_BIND_SERVICE", "CHOWN", "SETGID", "SETUID"}
        unexpected = [cap for cap in cap_add if cap not in allowed]
        if unexpected:
            failures.append(f"{name}: cap_add includes {unexpected}")

    volumes = svc.get("volumes") or []
    for vol in volumes:
        if isinstance(vol, str):
            if "/var/run/docker.sock" in vol:
                failures.append(f"{name}: docker.sock should not be mounted")
        elif isinstance(vol, dict):
            if vol.get("source") == "/var/run/docker.sock":
                failures.append(f"{name}: docker.sock should not be mounted")

    image = svc.get("image")
    if isinstance(image, str) and image:
        if ":" not in image or image.endswith(":latest"):
            failures.append(f"{name}: image tag should be pinned (avoid :latest)")


def test_compose_static_security():
    # Static lint of compose files to enforce hardened service settings.
    if os.getenv("SKIP_COMPOSE_STATIC_CHECKS"):
        pytest.skip("Static compose checks disabled")

    failures = []
    try:
        for path in COMPOSE_FILES:
            if not path.exists():
                continue
            data = _load_yaml(path)
            services = data.get("services") or {}
            for name, svc in services.items():
                _check_service(f"{path.name}:{name}", svc, failures)
    except RuntimeError as exc:
        pytest.skip(str(exc))

    assert not failures, "\n".join(failures)


def test_compose_internal_ip_reservations():
    # Prevent dynamic/static IP collisions on docker_internal.
    base = _load_yaml(ROOT / "compose/base.yml")
    chatbot_provider = _load_yaml(ROOT / "compose/modes/frontend.provider.yml")
    api = _load_yaml(ROOT / "compose/features/api_egress.yml")
    ldap = _load_yaml(ROOT / "compose/features/ldap.yml")

    ipam_config = _get_nested(base, ["networks", "docker_internal", "ipam", "config"], [])
    assert isinstance(ipam_config, list) and ipam_config, "base.yml missing docker_internal ipam config"
    first_pool = ipam_config[0] if isinstance(ipam_config[0], dict) else {}
    assert first_pool.get("ip_range") == "172.16.238.128/25", (
        "docker_internal ip_range must reserve low static IPs and keep dynamic pool non-overlapping"
    )

    frontend_ip = _get_nested(
        chatbot_provider, ["services", "frontend", "networks", "docker_internal", "ipv4_address"], ""
    )
    ingress_ip = _get_nested(
        base, ["services", "ingress", "networks", "docker_internal", "ipv4_address"], ""
    )
    api_ip = _get_nested(
        api, ["services", "api_egress", "networks", "docker_internal", "ipv4_address"], ""
    )
    ldap_ip = _get_nested(
        ldap, ["services", "ldap_egress", "networks", "docker_internal", "ipv4_address"], ""
    )

    assert "172.16.238.10" in str(frontend_ip), "frontend must keep static docker_internal IP .10"
    assert "172.16.238.11" in str(ingress_ip), "ingress must keep static docker_internal IP .11"
    assert "172.16.238.12" in str(api_ip), "api_egress must keep static docker_internal IP .12"
    assert "172.16.238.13" in str(ldap_ip), "ldap_egress must keep static docker_internal IP .13"


def test_ingress_tmpfs_includes_tls_secret_path():
    base = _load_yaml(ROOT / "compose/base.yml")
    tmpfs = _get_nested(base, ["services", "ingress", "tmpfs"], [])

    assert any(
        str(entry) == "/run/secrets:uid=101,gid=101,mode=700"
        for entry in tmpfs
    ), "ingress tmpfs must include a writable in-memory /run/secrets mount for TLS key injection"


def test_model_base_worker_template_has_http_healthcheck():
    model_base = _load_yaml(ROOT / "compose/model.base.yml")
    healthcheck = _get_nested(model_base, ["services", "base_worker_template", "healthcheck"], {})
    test_cmd = healthcheck.get("test") or []

    assert isinstance(test_cmd, list) and test_cmd[:2] == ["CMD", "python3"], (
        "base_worker_template must define a Python-based Docker healthcheck"
    )
    assert any("/health" in str(entry) for entry in test_cmd), (
        "base_worker_template healthcheck must probe the local /health endpoint"
    )


def test_ldap_overlay_requires_root_ca_and_internal_bind():
    ldap = _load_yaml(ROOT / "compose/features/ldap.yml")
    env = _get_nested(ldap, ["services", "ldap_egress", "environment"], [])
    volumes = _get_nested(ldap, ["services", "ldap_egress", "volumes"], [])

    assert any("LDAP_LISTEN_IP=172.16.238.13" in str(entry) for entry in env), (
        "ldap_egress must export LDAP_LISTEN_IP with its fixed docker_internal IP"
    )
    assert any(
        str(entry.get("source", "")).startswith("${ROOT_CA_PATH:?")
        for entry in volumes
        if isinstance(entry, dict)
    ), "ldap_egress must require ROOT_CA_PATH instead of defaulting to /dev/null"


def test_rendered_model_compose_static_security(tmp_path):
    schema = env_schema.load_env_schema(str(ROOT), strict=True)
    resolved = model_deployment.resolve_model_deployment(
        schema=schema,
        config_path=str(ROOT / "tests" / "model_deployments" / "gpt-oss-2x2.toml"),
        config_var="MODEL_DEPLOYMENT_CONFIG",
        runtime_mode="chatbot_provider",
        generated_compose_path=str(tmp_path / "model.llm.yml"),
        gpu_inventory={
            0: model_deployment.LocalGpuInfo(0, "A100", "nvidia_ampere"),
            1: model_deployment.LocalGpuInfo(1, "A100", "nvidia_ampere"),
            2: model_deployment.LocalGpuInfo(2, "A100", "nvidia_ampere"),
            3: model_deployment.LocalGpuInfo(3, "A100", "nvidia_ampere"),
        },
    )
    rendered = model_deployment.render_model_compose(
        resolved,
        output_path=str(tmp_path / "model.llm.yml"),
    )

    failures = []
    for name, svc in (rendered.get("services") or {}).items():
        _check_service(f"rendered:{name}", svc, failures)

    assert not failures, "\n".join(failures)
