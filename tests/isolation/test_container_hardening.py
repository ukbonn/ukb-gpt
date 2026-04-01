import pytest

from tests.helpers.docker import inspect_container, list_project_containers

pytestmark = [
    pytest.mark.isolation,
    pytest.mark.chatbot_provider,
    pytest.mark.batch_client,
]


def _has_docker_sock(mounts: list) -> bool:
    for mount in mounts or []:
        if mount.get("Source") == "/var/run/docker.sock":
            return True
    return False


def _backend_like_containers() -> list[str]:
    containers = list_project_containers("ukbgpt")
    return sorted(
        name
        for name in containers
        if (
            name.startswith("ukbgpt_worker_")
            or name.startswith("ukbgpt_embedding_worker_")
            or name.startswith("ukbgpt_stt_worker_")
            or name.startswith("ukbgpt_tts_worker_")
            or name == "ukbgpt_backend_router"
            or name == "ukbgpt_embedding_backend_router"
            or name == "ukbgpt_stt_backend_router"
            or name == "ukbgpt_tts_backend_router"
        )
    )


def test_no_privileged_or_host_modes(stack):
    # Containers must avoid privileged/host namespaces and docker.sock mounts.
    _ = stack
    containers = list_project_containers("ukbgpt")
    assert containers, "No project containers found; is the stack running?"

    failures = []
    for name in containers:
        data = inspect_container(name)
        host = data.get("HostConfig", {}) or {}
        if host.get("Privileged"):
            failures.append(f"{name} is running privileged")
        if host.get("NetworkMode") == "host":
            failures.append(f"{name} uses host network mode")
        if host.get("PidMode") == "host":
            failures.append(f"{name} uses host pid namespace")
        if host.get("IpcMode") == "host":
            failures.append(f"{name} uses host ipc namespace")
        if _has_docker_sock(data.get("Mounts", [])):
            failures.append(f"{name} mounts /var/run/docker.sock")

    assert not failures, "\n".join(failures)


def test_cap_drop_and_no_new_privs_for_hardened(stack):
    # Hardened services must drop all caps and set no-new-privileges.
    _ = stack
    hardened = [
        "ukbgpt_ingress",
        "ukbgpt_frontend",
        "ukbgpt_pipelines",
        "ukbgpt_exporter",
        # Optional services (present depending on mode/feature flags)
        "ukbgpt_ldap_egress",
        "ukbgpt_api_egress",
        "ukbgpt_prometheus",
        "ukbgpt_grafana",
        "ukbgpt_chat_purger",
    ]
    hardened.extend(_backend_like_containers())

    failures = []
    for name in hardened:
        try:
            data = inspect_container(name)
        except AssertionError:
            continue
        host = data.get("HostConfig", {}) or {}
        cap_drop = host.get("CapDrop") or []
        if "ALL" not in cap_drop:
            failures.append(f"{name} missing CapDrop=ALL")
        security_opt = host.get("SecurityOpt") or []
        if not any("no-new-privileges" in opt for opt in security_opt):
            failures.append(f"{name} missing no-new-privileges")

    assert not failures, "\n".join(failures)


def test_readonly_rootfs_recommendation(stack):
    # Enforce read-only rootfs for deterministic infra services.
    # App runtimes (frontend/worker) are intentionally writable for stability.
    _ = stack
    hardened = [
        "ukbgpt_ingress",
        "ukbgpt_pipelines",
        "ukbgpt_exporter",
        # Optional services (present depending on mode/feature flags)
        "ukbgpt_ldap_egress",
        "ukbgpt_api_egress",
        "ukbgpt_prometheus",
        "ukbgpt_grafana",
        "ukbgpt_chat_purger",
    ]

    failures = []
    for name in hardened:
        try:
            data = inspect_container(name)
        except AssertionError:
            continue
        host = data.get("HostConfig", {}) or {}
        if not host.get("ReadonlyRootfs", False):
            failures.append(f"{name} does not use read-only rootfs")

    assert not failures, "\n".join(failures)


def test_non_root_user_recommendation(stack):
    # Enforce non-root users in key user-facing containers.
    # Worker images are currently excluded due runtime/GPU compatibility constraints.
    _ = stack
    containers = [
        "ukbgpt_ingress",
        "ukbgpt_frontend",
        "ukbgpt_pipelines",
        "ukbgpt_exporter",
    ]

    failures = []
    for name in containers:
        try:
            data = inspect_container(name)
        except AssertionError:
            continue
        user = (data.get("Config", {}) or {}).get("User") or ""
        if not user:
            failures.append(f"{name} runs as root (Config.User is empty)")

    assert not failures, "\n".join(failures)
