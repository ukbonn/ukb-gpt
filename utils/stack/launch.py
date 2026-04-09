import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable

from ..models.deployment import (
    ResolvedModelDeployment,
    router_container_name,
    router_service_name,
    worker_container_name,
    worker_service_name,
)

_MODEL_WORKER_HEALTH_TIMEOUT_SECONDS = 1800
_MODEL_WORKER_HEALTH_POLL_INTERVAL_SECONDS = 5
_ROUTER_HEALTH_TIMEOUT_SECONDS = 120
_CONTAINER_LOG_TAIL_LINES = 60
_ROLE_START_PRIORITIES = {"stt": 0, "tts": 1, "embedding": 2, "llm": 3}
_ROLE_LABELS = {"llm": "LLM", "embedding": "Embedding", "stt": "STT", "tts": "TTS"}


@dataclass(frozen=True)
class BackendRoleDiscovery:
    workers: tuple[str, ...] = ()
    router_services: tuple[str, ...] = ()
    backend_nodes: str = ""
    endpoint: str = ""
    bypass_router: str = "true"


@dataclass(frozen=True)
class BackendDiscovery:
    llm: BackendRoleDiscovery
    embedding: BackendRoleDiscovery
    stt: BackendRoleDiscovery
    tts: BackendRoleDiscovery

    @property
    def runtime_services(self) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for backend in (self.llm, self.embedding, self.stt, self.tts):
            for service in (*backend.router_services, *backend.workers):
                if service in seen:
                    continue
                seen.add(service)
                ordered.append(service)
        return tuple(ordered)


@dataclass(frozen=True)
class StartupWorkerTarget:
    role: str
    worker_index: int
    service_name: str
    container_name: str
    gpus: tuple[int, ...]


@dataclass(frozen=True)
class StartupRouterTarget:
    role: str
    service_name: str
    container_name: str


@dataclass(frozen=True)
class StartupLaunchPlan:
    core_services: tuple[str, ...]
    uncontended_workers: tuple[StartupWorkerTarget, ...]
    shared_gpu_worker_phases: tuple[tuple[StartupWorkerTarget, ...], ...]
    routers: tuple[StartupRouterTarget, ...]


def _ordered_unique(values: list[str] | tuple[str, ...]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _role_start_priority(role: str) -> int:
    return _ROLE_START_PRIORITIES.get(role, len(_ROLE_START_PRIORITIES))


def _worker_sort_key(target: StartupWorkerTarget) -> tuple[int, int, str]:
    return (_role_start_priority(target.role), target.worker_index, target.service_name)


def _router_sort_key(target: StartupRouterTarget) -> tuple[int, str]:
    return (_role_start_priority(target.role), target.service_name)


def _workers_share_gpu(left: StartupWorkerTarget, right: StartupWorkerTarget) -> bool:
    if not left.gpus or not right.gpus:
        return False
    return not set(left.gpus).isdisjoint(right.gpus)


def plan_service_startup(
    core_services: list[str],
    discovery: BackendDiscovery,
    resolved_deployments: tuple[ResolvedModelDeployment, ...],
) -> StartupLaunchPlan:
    discovered_worker_services = set(
        discovery.llm.workers
        + discovery.embedding.workers
        + discovery.stt.workers
        + discovery.tts.workers
    )
    discovered_router_services = set(
        discovery.llm.router_services
        + discovery.embedding.router_services
        + discovery.stt.router_services
        + discovery.tts.router_services
    )

    worker_targets: list[StartupWorkerTarget] = []
    router_targets: list[StartupRouterTarget] = []
    for deployment in sorted(resolved_deployments, key=lambda item: _role_start_priority(item.role)):
        if deployment.router_enabled:
            service_name = router_service_name(deployment.role)
            if not discovered_router_services or service_name in discovered_router_services:
                router_targets.append(
                    StartupRouterTarget(
                        role=deployment.role,
                        service_name=service_name,
                        container_name=router_container_name(deployment.role),
                    )
                )

        for worker_index, worker in enumerate(deployment.workers):
            service_name = worker_service_name(deployment.role, worker_index)
            if discovered_worker_services and service_name not in discovered_worker_services:
                continue
            worker_targets.append(
                StartupWorkerTarget(
                    role=deployment.role,
                    worker_index=worker_index,
                    service_name=service_name,
                    container_name=worker_container_name(deployment.role, worker_index),
                    gpus=worker.gpus,
                )
            )

    uncontended_workers: list[StartupWorkerTarget] = []
    shared_gpu_groups: list[tuple[StartupWorkerTarget, ...]] = []
    visited: set[int] = set()
    for start_index, target in enumerate(worker_targets):
        if start_index in visited:
            continue
        if not target.gpus:
            visited.add(start_index)
            uncontended_workers.append(target)
            continue

        stack = [start_index]
        component: list[StartupWorkerTarget] = []
        while stack:
            current_index = stack.pop()
            if current_index in visited:
                continue
            visited.add(current_index)
            current_target = worker_targets[current_index]
            component.append(current_target)
            for candidate_index, candidate_target in enumerate(worker_targets):
                if candidate_index in visited:
                    continue
                if _workers_share_gpu(current_target, candidate_target):
                    stack.append(candidate_index)

        if len(component) == 1:
            uncontended_workers.extend(component)
        else:
            shared_gpu_groups.append(tuple(sorted(component, key=_worker_sort_key)))

    shared_gpu_groups.sort(
        key=lambda group: tuple(_worker_sort_key(target) for target in group)
    )
    uncontended_workers = sorted(uncontended_workers, key=_worker_sort_key)

    shared_gpu_worker_phases: list[tuple[StartupWorkerTarget, ...]] = []
    if shared_gpu_groups:
        max_group_len = max(len(group) for group in shared_gpu_groups)
        for round_index in range(max_group_len):
            round_targets = [
                group[round_index]
                for group in shared_gpu_groups
                if round_index < len(group)
            ]
            if round_targets:
                shared_gpu_worker_phases.append(tuple(sorted(round_targets, key=_worker_sort_key)))

    unique_router_targets: list[StartupRouterTarget] = []
    seen_router_services: set[str] = set()
    for target in sorted(router_targets, key=_router_sort_key):
        if target.service_name in seen_router_services:
            continue
        seen_router_services.add(target.service_name)
        unique_router_targets.append(target)

    return StartupLaunchPlan(
        core_services=tuple(_ordered_unique(core_services)),
        uncontended_workers=tuple(uncontended_workers),
        shared_gpu_worker_phases=tuple(shared_gpu_worker_phases),
        routers=tuple(unique_router_targets),
    )


def _docker_inspect_field(container_name: str, field: str) -> str:
    result = subprocess.run(
        ["docker", "inspect", "-f", field, container_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _print_container_log_tail(container_name: str) -> None:
    result = subprocess.run(
        ["docker", "logs", "--tail", str(_CONTAINER_LOG_TAIL_LINES), container_name],
        capture_output=True,
        text=True,
        check=False,
    )
    logs = (result.stdout or "") + (result.stderr or "")
    if not logs.strip():
        return
    print(f"--- Recent logs: {container_name} ---")
    print(logs.rstrip())


def _start_service_batch(
    compose_args,
    services,
    batch_mode: bool,
    *,
    format_cmd: Callable[[list[str]], str],
    print_subprocess_output: Callable[[subprocess.CompletedProcess], None],
    port_listener_diagnostics_callback: Callable[[bool], None] | None,
) -> None:
    start_cmd = [
        "docker",
        "compose",
        *compose_args,
        "up",
        "-d",
        "--no-build",
        "--no-deps",
        *services,
    ]
    print(f"  [EXEC] {format_cmd(start_cmd)}")
    result = subprocess.run(start_cmd, capture_output=True, text=True)
    print_subprocess_output(result)
    if result.returncode == 0:
        return

    combined_output = f"{result.stdout}\n{result.stderr}"
    if "Address already in use" in combined_output:
        print("\n❌ Error: Ingress failed to bind a host port (Address already in use).")
        if port_listener_diagnostics_callback is not None:
            port_listener_diagnostics_callback(batch_mode)
        print("   Action: stop the conflicting listener(s) or change ingress published ports.")
        sys.exit(1)

    print(f"\n❌ Error: Command failed: {format_cmd(start_cmd)}")
    sys.exit(1)


def _wait_for_container_health(
    container_name: str,
    *,
    label: str,
    timeout_seconds: int,
    shared_gpu_phase: bool = False,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = _docker_inspect_field(container_name, "{{.State.Status}}")
        if state in {"exited", "dead"}:
            print(f"\n❌ Error: {label} exited before becoming healthy.")
            if shared_gpu_phase:
                print(
                    "   This worker was being launched in a shared-GPU phase. "
                    "A common cause is vLLM startup-time GPU memory pressure from colocated workers."
                )
            _print_container_log_tail(container_name)
            sys.exit(1)

        health = _docker_inspect_field(
            container_name,
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}",
        )
        if health == "healthy":
            return
        if health == "unhealthy":
            print(f"\n❌ Error: {label} became unhealthy during startup.")
            if shared_gpu_phase:
                print(
                    "   This worker was being launched in a shared-GPU phase. "
                    "A common cause is vLLM startup-time GPU memory pressure from colocated workers."
                )
            _print_container_log_tail(container_name)
            sys.exit(1)
        if state == "running" and health == "missing":
            print(f"\n❌ Error: {label} is missing a Docker healthcheck.")
            print("   Model workers must expose /health through the shared worker template.")
            sys.exit(1)

        time.sleep(_MODEL_WORKER_HEALTH_POLL_INTERVAL_SECONDS)

    print(f"\n❌ Error: Timed out waiting for {label} to become healthy.")
    if shared_gpu_phase:
        print(
            "   This worker was being launched in a shared-GPU phase. "
            "If this is a colocated vLLM deployment, check for startup-time GPU memory pressure."
        )
    _print_container_log_tail(container_name)
    sys.exit(1)


def _wait_for_worker_targets(
    targets: tuple[StartupWorkerTarget, ...],
    *,
    shared_gpu_phase: bool,
) -> None:
    for target in targets:
        _wait_for_container_health(
            target.container_name,
            label=f"{_ROLE_LABELS.get(target.role, target.role)} worker {target.service_name}",
            timeout_seconds=_MODEL_WORKER_HEALTH_TIMEOUT_SECONDS,
            shared_gpu_phase=shared_gpu_phase,
        )


def _wait_for_router_targets(targets: tuple[StartupRouterTarget, ...]) -> None:
    for target in targets:
        _wait_for_container_health(
            target.container_name,
            label=f"{_ROLE_LABELS.get(target.role, target.role)} backend router {target.service_name}",
            timeout_seconds=_ROUTER_HEALTH_TIMEOUT_SECONDS,
        )


def start_services(
    compose_args,
    launch_plan: StartupLaunchPlan,
    batch_mode: bool,
    *,
    format_cmd: Callable[[list[str]], str],
    print_subprocess_output: Callable[[subprocess.CompletedProcess], None],
    port_listener_diagnostics_callback: Callable[[bool], None] | None = None,
) -> None:
    if launch_plan.core_services:
        print("\n--> Starting Core Services...")
        _start_service_batch(
            compose_args,
            list(launch_plan.core_services),
            batch_mode,
            format_cmd=format_cmd,
            print_subprocess_output=print_subprocess_output,
            port_listener_diagnostics_callback=port_listener_diagnostics_callback,
        )

    if launch_plan.uncontended_workers:
        print("\n--> Starting Uncontended Model Workers...")
        _start_service_batch(
            compose_args,
            [target.service_name for target in launch_plan.uncontended_workers],
            batch_mode,
            format_cmd=format_cmd,
            print_subprocess_output=print_subprocess_output,
            port_listener_diagnostics_callback=port_listener_diagnostics_callback,
        )
        _wait_for_worker_targets(launch_plan.uncontended_workers, shared_gpu_phase=False)

    for phase_index, phase_targets in enumerate(launch_plan.shared_gpu_worker_phases, start=1):
        print(f"\n--> Starting Shared-GPU Worker Round {phase_index}...")
        _start_service_batch(
            compose_args,
            [target.service_name for target in phase_targets],
            batch_mode,
            format_cmd=format_cmd,
            print_subprocess_output=print_subprocess_output,
            port_listener_diagnostics_callback=port_listener_diagnostics_callback,
        )
        _wait_for_worker_targets(phase_targets, shared_gpu_phase=True)

    if launch_plan.routers:
        print("\n--> Starting Backend Routers...")
        _start_service_batch(
            compose_args,
            [target.service_name for target in launch_plan.routers],
            batch_mode,
            format_cmd=format_cmd,
            print_subprocess_output=print_subprocess_output,
            port_listener_diagnostics_callback=port_listener_diagnostics_callback,
        )
        _wait_for_router_targets(launch_plan.routers)
