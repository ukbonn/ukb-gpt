import pytest

from tests.helpers.docker import inspect_container, list_project_containers

pytestmark = [
    pytest.mark.isolation,
    pytest.mark.chatbot_provider,
    pytest.mark.batch_client,
]


def _networks_for(container: str) -> set:
    data = inspect_container(container)
    networks = data.get("NetworkSettings", {}).get("Networks", {}) or {}
    names = set()
    for name in networks.keys():
        if name.startswith("ukbgpt_"):
            names.add(name[len("ukbgpt_"):])
        else:
            names.add(name)
    return names


def _is_enabled(env: dict, key: str) -> bool:
    return env.get(key, "false").strip().lower() == "true"


def _backend_containers() -> list[str]:
    containers = list_project_containers("ukbgpt")
    return sorted(
        name
        for name in containers
        if (
            name.startswith("ukbgpt_worker_")
            or name.startswith("ukbgpt_embedding_worker_")
            or name.startswith("ukbgpt_stt_worker_")
            or name == "ukbgpt_backend_router"
            or name == "ukbgpt_embedding_backend_router"
            or name == "ukbgpt_stt_backend_router"
        )
    )


def test_expected_network_attachments(stack):
    # Enforce strict network segmentation per mode.
    env = stack.env
    if stack.mode == "chatbot_provider":
        expected = {
            "ukbgpt_ingress": {"docker_internal", "dmz_ingress"},
            "ukbgpt_frontend": {"docker_internal"},
        }
        if _is_enabled(env, "ENABLE_RATE_LIMITING"):
            expected["ukbgpt_pipelines"] = {"docker_internal"}
        for backend in _backend_containers():
            expected[backend] = {"docker_internal"}

        if _is_enabled(env, "ENABLE_LDAP"):
            expected["ukbgpt_ldap_egress"] = {"docker_internal", "dmz_egress"}

        if _is_enabled(env, "ENABLE_INTERNAL_METRICS"):
            expected["ukbgpt_exporter"] = {"docker_internal"}
            expected["ukbgpt_prometheus"] = {"docker_internal"}
            expected["ukbgpt_grafana"] = {"docker_internal"}

        if _is_enabled(env, "ENABLE_CHAT_PURGER"):
            expected["ukbgpt_chat_purger"] = {"docker_internal"}
        if _is_enabled(env, "ENABLE_DICTATION_APP"):
            expected["ukbgpt_dictation"] = {"docker_internal"}
    else:
        expected = {
            "ukbgpt_ingress": {"docker_internal", "dmz_ingress"},
        }
        for backend in _backend_containers():
            expected[backend] = {"docker_internal"}
        if _is_enabled(env, "ENABLE_INTERNAL_METRICS"):
            expected["ukbgpt_exporter"] = {"docker_internal"}
        if (
            env.get("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS", "").strip()
            or env.get("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS", "").strip()
        ):
            expected["ukbgpt_api_egress"] = {"docker_internal", "dmz_egress"}
        if _is_enabled(env, "ENABLE_DATASET_STRUCTURING_APP"):
            expected["ukbgpt_dataset_structuring"] = {"docker_internal"}
        if _is_enabled(env, "ENABLE_COHORT_FEASIBILITY_APP"):
            expected["ukbgpt_cohort_feasibility"] = {"docker_internal"}

    failures = []
    for container, expected_networks in expected.items():
        try:
            actual = _networks_for(container)
        except AssertionError:
            failures.append(f"Missing container: {container}")
            continue

        if actual != expected_networks:
            failures.append(
                f"{container} networks {sorted(actual)} != {sorted(expected_networks)}"
            )

    assert not failures, "\n".join(failures)
