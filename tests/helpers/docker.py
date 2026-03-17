import json
import os
import time
from pathlib import Path
from typing import List, Mapping, Optional

from tests.helpers.commands import CmdResult, assert_ok, run


_DISABLED_MODEL_DEPLOYMENT_VALUES = {"", "none", "off", "disable", "disabled"}
_GENERATED_MODEL_COMPOSE_FILES = (
    ("MODEL_DEPLOYMENT_CONFIG", "compose/generated/model.llm.yml"),
    ("EMBEDDING_MODEL_DEPLOYMENT_CONFIG", "compose/generated/model.embedding.yml"),
    ("STT_MODEL_DEPLOYMENT_CONFIG", "compose/generated/model.stt.yml"),
)


def compose_flags(*compose_files: str | Path) -> List[str]:
    flags: List[str] = []
    for compose_file in compose_files:
        flags.extend(["-f", str(compose_file)])
    return flags


def compose_flags_with_generated_models(
    root_dir: str | Path,
    *compose_files: str | Path,
    env: Mapping[str, str] | None = None,
) -> List[str]:
    lookup = os.environ if env is None else env
    root_path = Path(root_dir)
    model_compose_files: List[Path] = []

    for env_name, relative_path in _GENERATED_MODEL_COMPOSE_FILES:
        value = (lookup.get(env_name) or "").strip()
        if value.lower() in _DISABLED_MODEL_DEPLOYMENT_VALUES:
            continue

        compose_path = root_path / relative_path
        if compose_path.is_file():
            model_compose_files.append(compose_path)

    return compose_flags(*compose_files, *model_compose_files)


def compose_command(compose_flags: List[str], args: List[str]) -> List[str]:
    return ["docker", "compose"] + compose_flags + args


def compose(compose_flags: List[str], args: List[str], env: Optional[dict] = None) -> CmdResult:
    cmd = compose_command(compose_flags, args)
    return run(cmd, shell=False, env=env)


def compose_services(compose_flags: List[str], env: Optional[dict] = None) -> List[str]:
    res = compose(compose_flags, ["config", "--services"], env=env)
    assert_ok(res, "Failed to list compose services")
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def dump_compose_logs(
    compose_flags: List[str],
    log_dir: Path,
    *,
    prefix: str = "",
    env: Optional[dict] = None,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    try:
        services = compose_services(compose_flags, env=env)
    except AssertionError as exc:
        error_log = log_dir / f"{prefix}compose_services_error.log"
        with error_log.open("w", encoding="utf-8") as handle:
            handle.write(str(exc))
        return

    for svc in services:
        filename = f"{prefix}{svc}.log" if prefix else f"{svc}.log"
        path = log_dir / filename
        with path.open("w", encoding="utf-8") as handle:
            res = compose(compose_flags, ["logs", "--no-color", svc], env=env)
            handle.write(res.stdout or "")
            if res.stderr:
                handle.write("\n" + res.stderr)


def dump_container_logs(container: str, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    res = run(["docker", "logs", "--tail=1000", container], shell=False)
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(res.stdout or "")
        if res.stderr:
            handle.write("\n" + res.stderr)


def list_project_containers(project_name: str) -> List[str]:
    res = run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--format",
            "{{.Names}}",
        ],
        shell=False,
    )
    if res.code != 0:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def inspect_container(name: str) -> dict:
    res = run(["docker", "inspect", name], shell=False)
    assert_ok(res, f"Failed to inspect container {name}")
    data = json.loads(res.stdout)
    if not data:
        raise AssertionError(f"No inspect data for {name}")
    return data[0]


def find_ingress_container(project_name: str) -> Optional[str]:
    res = run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--filter",
            "label=com.docker.compose.service=ingress",
            "--format",
            "{{.Names}}",
        ],
        shell=False,
    )
    if res.code != 0:
        return None
    return res.stdout.splitlines()[0].strip() if res.stdout.strip() else None


def ensure_dmz_egress_network() -> Optional[str]:
    network_name = "ukbgpt_dmz_egress"
    candidate_subnets = [
        "172.20.0.0/24",
        "172.21.0.0/24",
        "172.30.0.0/24",
        "172.31.0.0/24",
    ]

    inspect = run(["docker", "network", "inspect", network_name], shell=False)
    if inspect.code == 0:
        try:
            data = json.loads(inspect.stdout)
            subnet = data[0]["IPAM"]["Config"][0].get("Subnet")
            labels = data[0].get("Labels", {}) or {}
            has_labels = (
                labels.get("com.docker.compose.project") == "ukbgpt"
                and labels.get("com.docker.compose.network") == "dmz_egress"
            )
            if subnet in candidate_subnets and has_labels:
                return subnet

            run(["docker", "network", "rm", network_name], shell=False)
        except Exception:
            return None

    for subnet in candidate_subnets:
        res = run(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--opt",
                "com.docker.network.bridge.name=br-dmz-egress",
                "--label",
                "com.docker.compose.project=ukbgpt",
                "--label",
                "com.docker.compose.network=dmz_egress",
                "--subnet",
                subnet,
                network_name,
            ],
            shell=False,
        )
        if res.code == 0:
            return subnet

    return None


def ensure_container_ip(container_name: str, network_name: str, desired_ip: str) -> bool:
    inspect = run(["docker", "inspect", container_name], shell=False)
    if inspect.code != 0:
        return False

    try:
        data = json.loads(inspect.stdout)
        networks = data[0].get("NetworkSettings", {}).get("Networks", {}) or {}
        current_ip = networks.get(network_name, {}).get("IPAddress")
    except Exception:
        return False

    if current_ip == desired_ip:
        return True

    if current_ip:
        run(["docker", "network", "disconnect", network_name, container_name], shell=False)

    connect = run(
        ["docker", "network", "connect", "--ip", desired_ip, network_name, container_name],
        shell=False,
    )
    return connect.code == 0


def wait_for_container_health(container_name: str, max_retries: int = 30) -> bool:
    for _ in range(max_retries):
        res = run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Health.Status}}",
                container_name,
            ],
            shell=False,
        )
        status = res.stdout.strip()
        if status == "healthy":
            return True
        if status == "unhealthy":
            return False
        time.sleep(1)
    return True
