import pytest

from tests.helpers.docker import inspect_container

pytestmark = [
    pytest.mark.isolation,
    pytest.mark.batch_client,
    pytest.mark.usefixtures("batch_client_feasibility_stack"),
]


def test_cohort_feasibility_container_is_internal_only():
    data = inspect_container("ukbgpt_cohort_feasibility")
    networks = data.get("NetworkSettings", {}).get("Networks", {}) or {}
    names = sorted(
        name[len("ukbgpt_"):] if name.startswith("ukbgpt_") else name
        for name in networks.keys()
    )
    assert names == ["docker_internal"]

    ports = data.get("HostConfig", {}).get("PortBindings")
    assert not ports, "cohort_feasibility should not publish host ports"
