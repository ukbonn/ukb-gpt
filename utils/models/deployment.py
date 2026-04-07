import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - exercised on older Python
    import tomli as tomllib  # type: ignore

from ..stack.schema import (
    EnvSchema,
    ModelArchitecturePresetSpec,
    ModelFamilySpec,
    resolve_model_family_from_id,
)


_ALLOWED_ROLES = {"llm", "embedding", "stt", "tts"}
_ALLOWED_GPU_ARCHITECTURES = {"auto", "default", "nvidia_ampere", "nvidia_hopper", "nvidia_blackwell"}
_ALLOWED_ROUTER_MODES = {"auto", "enabled", "disabled"}
REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEDULING_POLICY_FLAG = "--scheduling-policy"
_LLM_COMMAND_APPEND_ENV = "VLLM_LLM_COMMAND_APPEND"


@dataclass(frozen=True)
class LocalGpuInfo:
    index: int
    name: str
    gpu_architecture: str | None


@dataclass(frozen=True)
class ModelDeploymentWorkerDefaults:
    tensor_parallel_size: int | None
    data_parallel_size: int | None
    expert_parallel_enabled: bool | None


@dataclass(frozen=True)
class ModelDeploymentWorkerSpec:
    gpus: tuple[int, ...]
    tensor_parallel_size: int | None
    data_parallel_size: int | None
    expert_parallel_enabled: bool | None


@dataclass(frozen=True)
class ModelDeploymentSpec:
    api_version: str
    kind: str
    role: str
    model_family: str
    gpu_architecture: str
    router: str
    worker_defaults: ModelDeploymentWorkerDefaults
    workers: tuple[ModelDeploymentWorkerSpec, ...]
    source_path: str


@dataclass(frozen=True)
class WizardModelDeploymentOption:
    path: str
    env_value: str
    spec: ModelDeploymentSpec
    source: str


@dataclass(frozen=True)
class ResolvedDeploymentWorker:
    gpus: tuple[int, ...]
    tensor_parallel_size: int
    data_parallel_size: int
    expert_parallel_enabled: bool


@dataclass(frozen=True)
class ResolvedModelDeployment:
    role: str
    config_var: str
    config_path: str
    spec: ModelDeploymentSpec
    family: ModelFamilySpec
    resolved_gpu_architecture: str
    router_enabled: bool
    workers: tuple[ResolvedDeploymentWorker, ...]
    generated_compose_path: str


def parse_model_deployment(path: str | Path) -> ModelDeploymentSpec:
    resolved_path = Path(path).expanduser().resolve()
    if not resolved_path.is_file():
        raise ValueError(f"Model deployment config does not exist: {resolved_path}")

    with resolved_path.open("rb") as handle:
        raw = tomllib.load(handle)

    api_version = str(raw.get("api_version", "")).strip()
    kind = str(raw.get("kind", "")).strip()
    role = str(raw.get("role", "")).strip()
    model_family = str(raw.get("model_family", "")).strip()
    raw_gpu_architecture = raw.get("gpu_architecture")
    raw_legacy_architecture = raw.get("architecture")
    if raw_gpu_architecture is not None and raw_legacy_architecture is not None:
        raise ValueError(
            f"Deployment config {resolved_path} must not set both gpu_architecture and legacy architecture"
        )
    gpu_architecture = str(
        raw_gpu_architecture if raw_gpu_architecture is not None else raw_legacy_architecture or "auto"
    ).strip() or "auto"
    router = str(raw.get("router", "auto")).strip() or "auto"

    if api_version != "ukbgpt/v1alpha1":
        raise ValueError(
            f"Deployment config {resolved_path} must set api_version = \"ukbgpt/v1alpha1\""
        )
    if kind != "model_deployment":
        raise ValueError(f"Deployment config {resolved_path} must set kind = \"model_deployment\"")
    if role not in _ALLOWED_ROLES:
        raise ValueError(f"Deployment config {resolved_path} has invalid role: {role}")
    if not model_family:
        raise ValueError(f"Deployment config {resolved_path} is missing model_family")
    if gpu_architecture not in _ALLOWED_GPU_ARCHITECTURES:
        raise ValueError(
            f"Deployment config {resolved_path} has invalid gpu_architecture: {gpu_architecture}"
        )
    if router not in _ALLOWED_ROUTER_MODES:
        raise ValueError(f"Deployment config {resolved_path} has invalid router mode: {router}")

    raw_defaults = raw.get("worker_defaults", {})
    if raw_defaults is None:
        raw_defaults = {}
    if not isinstance(raw_defaults, dict):
        raise ValueError(f"Deployment config {resolved_path} has invalid [worker_defaults]")

    defaults = ModelDeploymentWorkerDefaults(
        tensor_parallel_size=_parse_optional_positive_int(
            raw_defaults.get("tensor_parallel_size"),
            label=f"{resolved_path} worker_defaults.tensor_parallel_size",
        ),
        data_parallel_size=_parse_optional_positive_int(
            raw_defaults.get("data_parallel_size"),
            label=f"{resolved_path} worker_defaults.data_parallel_size",
        ),
        expert_parallel_enabled=_parse_optional_bool(
            raw_defaults.get("expert_parallel_enabled"),
            label=f"{resolved_path} worker_defaults.expert_parallel_enabled",
        ),
    )

    raw_workers = raw.get("workers", [])
    if not isinstance(raw_workers, list) or not raw_workers:
        raise ValueError(f"Deployment config {resolved_path} must define at least one [[workers]] entry")

    workers: list[ModelDeploymentWorkerSpec] = []
    for idx, raw_worker in enumerate(raw_workers):
        if not isinstance(raw_worker, dict):
            raise ValueError(f"Deployment config {resolved_path} has invalid worker entry at index {idx}")

        raw_gpus = raw_worker.get("gpus", [])
        if raw_gpus is None:
            raw_gpus = []
        if not isinstance(raw_gpus, list):
            raise ValueError(f"Deployment config {resolved_path} worker {idx} has invalid gpus list")

        gpus: list[int] = []
        for raw_gpu in raw_gpus:
            if not isinstance(raw_gpu, int):
                raise ValueError(
                    f"Deployment config {resolved_path} worker {idx} gpu entries must be integers"
                )
            gpus.append(raw_gpu)

        workers.append(
            ModelDeploymentWorkerSpec(
                gpus=tuple(gpus),
                tensor_parallel_size=_parse_optional_positive_int(
                    raw_worker.get("tensor_parallel_size"),
                    label=f"{resolved_path} workers[{idx}].tensor_parallel_size",
                ),
                data_parallel_size=_parse_optional_positive_int(
                    raw_worker.get("data_parallel_size"),
                    label=f"{resolved_path} workers[{idx}].data_parallel_size",
                ),
                expert_parallel_enabled=_parse_optional_bool(
                    raw_worker.get("expert_parallel_enabled"),
                    label=f"{resolved_path} workers[{idx}].expert_parallel_enabled",
                ),
            )
        )

    return ModelDeploymentSpec(
        api_version=api_version,
        kind=kind,
        role=role,
        model_family=model_family,
        gpu_architecture=gpu_architecture,
        router=router,
        worker_defaults=defaults,
        workers=tuple(workers),
        source_path=str(resolved_path),
    )


def inspect_local_nvidia_gpus() -> dict[int, LocalGpuInfo]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {}

    primary_cmd = [
        nvidia_smi,
        "--query-gpu=index,name,compute_cap",
        "--format=csv,noheader,nounits",
    ]
    fallback_cmd = [
        nvidia_smi,
        "--query-gpu=index,name",
        "--format=csv,noheader,nounits",
    ]

    result = subprocess.run(primary_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        result = subprocess.run(fallback_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {}

    inventory: dict[int, LocalGpuInfo] = {}
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue
        if not parts[0].isdigit():
            continue
        index = int(parts[0])
        name = parts[1]
        compute_cap = parts[2] if len(parts) >= 3 else ""
        inventory[index] = LocalGpuInfo(
            index=index,
            name=name,
            gpu_architecture=normalize_nvidia_architecture(name=name, compute_cap=compute_cap),
        )

    return inventory


def normalize_nvidia_architecture(*, name: str, compute_cap: str = "") -> str | None:
    compute_cap = (compute_cap or "").strip()
    if compute_cap:
        major_raw = compute_cap.split(".", 1)[0]
        if major_raw.isdigit():
            major = int(major_raw)
            if major == 8:
                return "nvidia_ampere"
            if major == 9:
                return "nvidia_hopper"
            if major >= 10:
                return "nvidia_blackwell"

    lowered = (name or "").strip().lower()
    if not lowered:
        return None
    if "blackwell" in lowered or "rtx 6000 pro" in lowered:
        return "nvidia_blackwell"
    if any(token in lowered for token in ("hopper", "h100", "h200", "h20", "gh200")):
        return "nvidia_hopper"
    if any(token in lowered for token in ("ampere", "a100", "a40", "a30", "a10", "a16", "l4")):
        return "nvidia_ampere"
    return None


def resolve_model_deployment(
    *,
    schema: EnvSchema,
    config_path: str,
    config_var: str,
    runtime_mode: str,
    generated_compose_path: str,
    gpu_inventory: dict[int, LocalGpuInfo] | None = None,
) -> ResolvedModelDeployment:
    spec = parse_model_deployment(config_path)
    family = resolve_model_family_from_id(schema, spec.model_family)
    if family is None:
        raise ValueError(
            f"Deployment config {spec.source_path} references unknown model_family: {spec.model_family}"
        )
    if spec.role != family.role:
        raise ValueError(
            f"Deployment config {spec.source_path} role {spec.role!r} does not match model family role "
            f"{family.role!r}"
        )

    if family.accelerator == "nvidia":
        inventory = inspect_local_nvidia_gpus() if gpu_inventory is None else gpu_inventory
        if not inventory:
            raise ValueError(
                f"Deployment config {spec.source_path} requires NVIDIA GPUs, but no local GPU inventory "
                "could be discovered via nvidia-smi."
            )
    else:
        inventory = {}

    detected_gpu_architectures: set[str] = set()
    resolved_workers: list[ResolvedDeploymentWorker] = []
    seen_gpu_ids: set[int] = set()

    for worker_index, worker in enumerate(spec.workers):
        if family.accelerator == "nvidia":
            if not worker.gpus:
                raise ValueError(
                    f"Deployment config {spec.source_path} worker {worker_index} must declare at least one GPU"
                )
            for gpu_id in worker.gpus:
                if gpu_id in seen_gpu_ids:
                    raise ValueError(
                        f"Deployment config {spec.source_path} assigns GPU {gpu_id} to multiple workers"
                    )
                if gpu_id not in inventory:
                    raise ValueError(
                        f"Deployment config {spec.source_path} references unavailable GPU index {gpu_id}"
                    )
                seen_gpu_ids.add(gpu_id)
                detected_gpu_architecture = inventory[gpu_id].gpu_architecture
                if detected_gpu_architecture:
                    detected_gpu_architectures.add(detected_gpu_architecture)
        elif worker.gpus:
            raise ValueError(
                f"Deployment config {spec.source_path} worker {worker_index} declares GPUs for a non-GPU model family"
            )

        tensor_parallel_size = worker.tensor_parallel_size
        if tensor_parallel_size is None:
            tensor_parallel_size = spec.worker_defaults.tensor_parallel_size
        if tensor_parallel_size is None:
            tensor_parallel_size = len(worker.gpus) if worker.gpus else 1

        data_parallel_size = worker.data_parallel_size
        if data_parallel_size is None:
            data_parallel_size = spec.worker_defaults.data_parallel_size
        if data_parallel_size is None:
            data_parallel_size = 1

        if tensor_parallel_size <= 0:
            raise ValueError(
                f"Deployment config {spec.source_path} worker {worker_index} tensor_parallel_size must be positive"
            )
        if data_parallel_size <= 0:
            raise ValueError(
                f"Deployment config {spec.source_path} worker {worker_index} data_parallel_size must be positive"
            )

        required_gpu_count = tensor_parallel_size * data_parallel_size

        if family.accelerator == "nvidia" and required_gpu_count > len(worker.gpus):
            raise ValueError(
                f"Deployment config {spec.source_path} worker {worker_index} "
                f"tensor_parallel_size * data_parallel_size ({tensor_parallel_size} * {data_parallel_size} = "
                f"{required_gpu_count}) exceeds assigned GPU count ({len(worker.gpus)})"
            )
        if family.accelerator == "none" and tensor_parallel_size != 1:
            raise ValueError(
                f"Deployment config {spec.source_path} worker {worker_index} must use tensor_parallel_size=1 "
                "for non-GPU model families"
            )
        if family.accelerator == "none" and data_parallel_size != 1:
            raise ValueError(
                f"Deployment config {spec.source_path} worker {worker_index} must use data_parallel_size=1 "
                "for non-GPU model families"
            )

        expert_parallel_enabled = worker.expert_parallel_enabled
        if expert_parallel_enabled is None:
            expert_parallel_enabled = spec.worker_defaults.expert_parallel_enabled
        if expert_parallel_enabled is None:
            expert_parallel_enabled = False
        if expert_parallel_enabled and not family.supports_expert_parallel:
            raise ValueError(
                f"Deployment config {spec.source_path} enables expert parallelism, but "
                f"{family.title} does not support it"
            )

        resolved_workers.append(
            ResolvedDeploymentWorker(
                gpus=worker.gpus,
                tensor_parallel_size=tensor_parallel_size,
                data_parallel_size=data_parallel_size,
                expert_parallel_enabled=expert_parallel_enabled,
            )
        )

    if detected_gpu_architectures and len(detected_gpu_architectures) > 1:
        raise ValueError(
            f"Deployment config {spec.source_path} spans multiple GPU architectures: "
            f"{', '.join(sorted(detected_gpu_architectures))}"
        )

    detected_gpu_architecture = next(iter(detected_gpu_architectures), "")
    if spec.gpu_architecture == "auto":
        if family.accelerator == "nvidia" and not detected_gpu_architecture:
            raise ValueError(
                f"Deployment config {spec.source_path} uses gpu_architecture = \"auto\", but the selected GPUs "
                "could not be normalized to a supported GPU architecture"
            )
        resolved_gpu_architecture = detected_gpu_architecture or "default"
    else:
        resolved_gpu_architecture = spec.gpu_architecture
        if (
            detected_gpu_architecture
            and resolved_gpu_architecture != "default"
            and resolved_gpu_architecture != detected_gpu_architecture
        ):
            raise ValueError(
                f"Deployment config {spec.source_path} requested gpu_architecture {resolved_gpu_architecture}, "
                f"but the selected GPUs normalize to {detected_gpu_architecture}"
            )

    router_enabled = resolve_router_enabled(
        router_mode=spec.router,
        runtime_mode=runtime_mode,
        worker_count=len(resolved_workers),
    )

    return ResolvedModelDeployment(
        role=spec.role,
        config_var=config_var,
        config_path=spec.source_path,
        spec=spec,
        family=family,
        resolved_gpu_architecture=resolved_gpu_architecture,
        router_enabled=router_enabled,
        workers=tuple(resolved_workers),
        generated_compose_path=generated_compose_path,
    )


def resolve_router_enabled(*, router_mode: str, runtime_mode: str, worker_count: int) -> bool:
    if router_mode == "enabled":
        return True
    if router_mode == "disabled":
        return False
    if runtime_mode == "chatbot_provider":
        return worker_count > 1
    return False


def merged_architecture_preset(
    family: ModelFamilySpec,
    resolved_gpu_architecture: str,
) -> ModelArchitecturePresetSpec:
    default = family.architectures["default"]
    specific = family.architectures.get(resolved_gpu_architecture)
    if specific is None or resolved_gpu_architecture == "default":
        return default

    extra_volumes = list(default.extra_volumes)
    extra_volumes.extend(specific.extra_volumes)
    return ModelArchitecturePresetSpec(
        architecture_id=resolved_gpu_architecture,
        environment={**default.environment, **specific.environment},
        build_args={**default.build_args, **specific.build_args},
        command_append=default.command_append + specific.command_append,
        shm_size=specific.shm_size or default.shm_size,
        extra_volumes=tuple(extra_volumes),
    )


def render_model_compose(
    resolved: ResolvedModelDeployment,
    *,
    output_path: str,
) -> dict[str, object]:
    preset = merged_architecture_preset(resolved.family, resolved.resolved_gpu_architecture)
    runtime = resolved.family.runtime
    base_compose_file = resolved.family.base_compose_file
    if not os.path.isabs(base_compose_file):
        base_compose_file = str((REPO_ROOT / base_compose_file).resolve())
    base_path = _posix_path(str(Path(base_compose_file).resolve()))
    router_base_path = _posix_path(
        str((Path(base_compose_file).resolve().parents[2] / "backend_router.yml").resolve())
    )

    services: dict[str, object] = {}
    for index, worker in enumerate(resolved.workers):
        service_name = _worker_service_name(resolved.role, index)
        service: dict[str, object] = {
            "extends": {
                "file": _posix_path(base_path),
                "service": resolved.family.base_service,
            },
            "container_name": _container_name(resolved.role, index),
            "image": _worker_image_name(resolved.role, index),
        }

        build_args = dict(runtime.build_args)
        build_args.update(preset.build_args)
        if build_args:
            service["build"] = {"args": build_args}

        environment = dict(runtime.environment)
        environment.update(preset.environment)
        if environment:
            service["environment"] = environment

        shm_size = preset.shm_size or runtime.shm_size
        if shm_size:
            service["shm_size"] = shm_size

        command = list(runtime.command)
        command.extend(preset.command_append)
        if resolved.role == "llm":
            extra_command = os.getenv(_LLM_COMMAND_APPEND_ENV, "").strip()
            if extra_command:
                command.extend(shlex.split(extra_command))
        if (
            resolved.role == "llm"
            and not any(
                argument == _SCHEDULING_POLICY_FLAG
                or argument.startswith(f"{_SCHEDULING_POLICY_FLAG}=")
                for argument in command
            )
        ):
            command.append(f"{_SCHEDULING_POLICY_FLAG}=priority")
        if resolved.family.accelerator == "nvidia" and worker.expert_parallel_enabled:
            command.append("--enable-expert-parallel")
        if resolved.family.accelerator == "nvidia":
            command.append(f"--tensor-parallel-size={worker.tensor_parallel_size}")
            command.append(f"--data-parallel-size={worker.data_parallel_size}")
        elif worker.tensor_parallel_size != 1:
            command.append(f"--tensor-parallel-size={worker.tensor_parallel_size}")
        elif worker.data_parallel_size != 1:
            command.append(f"--data-parallel-size={worker.data_parallel_size}")
        if command:
            service["command"] = command

        volumes = [dict(volume) for volume in runtime.extra_volumes]
        volumes.extend(dict(volume) for volume in preset.extra_volumes)
        if volumes:
            service["volumes"] = volumes

        if worker.gpus:
            service["deploy"] = {
                "resources": {
                    "reservations": {
                        "devices": [
                            {
                                "driver": "nvidia",
                                "device_ids": [str(gpu_id) for gpu_id in worker.gpus],
                                "capabilities": ["gpu"],
                            }
                        ]
                    }
                }
            }

        services[service_name] = service

    if resolved.router_enabled:
        router_service_name = _router_service_name(resolved.role)
        services[router_service_name] = {
            "extends": {
                "file": _posix_path(router_base_path),
                "service": "backend_router",
            },
            "container_name": _router_container_name(resolved.role),
            "environment": {
                "CHECK_URL": "http://127.0.0.1:5000/health",
                "BACKEND_NODES": _router_backend_nodes(resolved.role, worker_count=len(resolved.workers)),
            },
        }

    return {"services": services}


def write_rendered_compose(path: str | Path, compose_data: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(compose_data, indent=2) + "\n", encoding="utf-8")


def create_wizard_model_deployment(
    *,
    family: ModelFamilySpec,
    role: str,
    role_label: str,
    root_dir: str | Path,
    prompt_choice: Callable[[str, list[str], int], int],
    prompt_yes_no: Callable[[str, bool], bool],
) -> str:
    root_path = Path(root_dir).resolve()
    deployment_path = next_wizard_model_deployment_path(
        root_path,
        role=role,
        family=family,
    )

    gpu_architecture = "default"
    worker_groups: list[tuple[int, ...]] = [tuple()]
    tensor_parallel_size: int | None = None
    data_parallel_size: int | None = None
    expert_parallel_enabled = False

    if family.accelerator == "nvidia":
        print("\nSelect GPU architecture preset:")
        gpu_architecture_options = [
            "auto",
            "nvidia_ampere",
            "nvidia_hopper",
            "nvidia_blackwell",
            "default",
        ]
        for idx, option in enumerate(gpu_architecture_options, start=1):
            print(f"{idx}. {option}")
        gpu_architecture = gpu_architecture_options[
            prompt_choice("GPU architecture [default 1]: ", gpu_architecture_options, 0)
        ]

        while True:
            raw_groups = input(
                "Set GPU groups (semicolon-separated workers, for example 0 or 0,1;2,3): "
            ).strip()
            if not raw_groups:
                raw_groups = "0"
            try:
                worker_groups = _parse_gpu_group_list(raw_groups)
                break
            except ValueError as exc:
                print(str(exc))

        while True:
            raw_tp = input(
                "Set default tensor parallel size [default: auto from worker GPU count]: "
            ).strip()
            if not raw_tp:
                break
            if raw_tp.isdigit() and int(raw_tp) > 0:
                tensor_parallel_size = int(raw_tp)
                break
            print("Please enter a positive integer or leave empty.")

        while True:
            raw_dp = input("Set default data parallel size [default: 1]: ").strip()
            if not raw_dp:
                break
            if raw_dp.isdigit() and int(raw_dp) > 0:
                data_parallel_size = int(raw_dp)
                break
            print("Please enter a positive integer or leave empty.")

        if family.supports_expert_parallel:
            expert_parallel_enabled = prompt_yes_no(
                f"Enable expert parallel for {role_label} workers?",
                False,
            )

    contents = render_model_deployment_toml(
        role=role,
        family=family,
        gpu_architecture=gpu_architecture,
        router="auto",
        worker_groups=worker_groups,
        tensor_parallel_size=tensor_parallel_size,
        data_parallel_size=data_parallel_size,
        expert_parallel_enabled=expert_parallel_enabled,
    )
    deployment_path.parent.mkdir(parents=True, exist_ok=True)
    deployment_path.write_text(contents, encoding="utf-8")
    env_value = _deployment_env_value(deployment_path, root_dir=root_path)
    print(f"Created deployment config: {env_value}")
    return env_value


def render_model_deployment_toml(
    *,
    role: str,
    family: ModelFamilySpec,
    gpu_architecture: str,
    router: str,
    worker_groups: list[tuple[int, ...]],
    tensor_parallel_size: int | None,
    data_parallel_size: int | None,
    expert_parallel_enabled: bool,
) -> str:
    lines = [
        'api_version = "ukbgpt/v1alpha1"',
        'kind = "model_deployment"',
        f'role = "{role}"',
        f'model_family = "{family.family_id}"',
        f'gpu_architecture = "{gpu_architecture}"',
        f'router = "{router}"',
        "",
        "[worker_defaults]",
    ]

    if tensor_parallel_size is not None:
        lines.append(f"tensor_parallel_size = {tensor_parallel_size}")
    if data_parallel_size not in (None, 1):
        lines.append(f"data_parallel_size = {data_parallel_size}")
    if expert_parallel_enabled:
        lines.append("expert_parallel_enabled = true")
    if lines[-1] == "[worker_defaults]":
        lines.pop()
    else:
        lines.append("")

    for group in worker_groups:
        lines.append("[[workers]]")
        gpu_values = ", ".join(str(gpu_id) for gpu_id in group)
        lines.append(f"gpus = [{gpu_values}]")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _parse_optional_positive_int(value: object, *, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _parse_optional_bool(value: object, *, label: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _worker_service_name(role: str, index: int) -> str:
    if role == "embedding":
        return f"embedding_worker_{index}"
    if role == "stt":
        return f"stt_worker_{index}"
    if role == "tts":
        return f"tts_worker_{index}"
    return f"worker_{index}"


def _container_name(role: str, index: int) -> str:
    if role == "embedding":
        return f"ukbgpt_embedding_worker_{index}"
    if role == "stt":
        return f"ukbgpt_stt_worker_{index}"
    if role == "tts":
        return f"ukbgpt_tts_worker_{index}"
    return f"ukbgpt_worker_{index}"


def _worker_image_name(role: str, index: int) -> str:
    if role == "embedding":
        return f"ukbgpt-embedding-worker-{index}:local"
    if role == "stt":
        return f"ukbgpt-stt-worker-{index}:local"
    if role == "tts":
        return f"ukbgpt-tts-worker-{index}:local"
    return f"ukbgpt-worker-{index}:local"


def _router_service_name(role: str) -> str:
    if role == "embedding":
        return "embedding_backend_router"
    if role == "stt":
        return "stt_backend_router"
    if role == "tts":
        return "tts_backend_router"
    return "backend_router"


def _router_container_name(role: str) -> str:
    if role == "embedding":
        return "ukbgpt_embedding_backend_router"
    if role == "stt":
        return "ukbgpt_stt_backend_router"
    if role == "tts":
        return "ukbgpt_tts_backend_router"
    return "ukbgpt_backend_router"


def _router_backend_nodes(role: str, *, worker_count: int) -> str:
    return ",".join(f"{_worker_service_name(role, index)}:5000" for index in range(worker_count))


def worker_service_name(role: str, index: int) -> str:
    return _worker_service_name(role, index)


def worker_container_name(role: str, index: int) -> str:
    return _container_name(role, index)


def router_service_name(role: str) -> str:
    return _router_service_name(role)


def router_container_name(role: str) -> str:
    return _router_container_name(role)


def _deployment_env_value(path: Path, *, root_dir: str | Path) -> str:
    root_path = Path(root_dir).resolve()
    try:
        return str(path.resolve().relative_to(root_path))
    except ValueError:
        return str(path.resolve())


def wizard_model_family_slug(family: ModelFamilySpec) -> str:
    return family.family_id.split(".", 2)[-1].replace(".", "_")


def wizard_model_deployment_dir(
    root_dir: str | Path,
    *,
    role: str,
    family: ModelFamilySpec,
) -> Path:
    root_path = Path(root_dir).resolve()
    return root_path / "compose" / "generated" / "deployments" / role / wizard_model_family_slug(family)


def next_wizard_model_deployment_path(
    root_dir: str | Path,
    *,
    role: str,
    family: ModelFamilySpec,
) -> Path:
    target_dir = wizard_model_deployment_dir(root_dir, role=role, family=family)
    index = 1
    while True:
        candidate = target_dir / f"deployment-{index:02d}.toml"
        if not candidate.exists():
            return candidate
        index += 1


def list_wizard_model_deployments(
    root_dir: str | Path,
    *,
    role: str,
    family: ModelFamilySpec,
) -> list[WizardModelDeploymentOption]:
    target_dir = wizard_model_deployment_dir(root_dir, role=role, family=family)
    if not target_dir.is_dir():
        return []

    root_path = Path(root_dir).resolve()
    discovered: list[WizardModelDeploymentOption] = []
    for path in sorted(target_dir.glob("*.toml")):
        try:
            spec = parse_model_deployment(path)
        except ValueError:
            continue
        if spec.role != role or spec.model_family != family.family_id:
            continue
        discovered.append(
            WizardModelDeploymentOption(
                path=str(path.resolve()),
                env_value=_deployment_env_value(path, root_dir=root_path),
                spec=spec,
                source="managed",
            )
        )
    return discovered


def model_deployment_summary(spec: ModelDeploymentSpec) -> str:
    worker_groups = ";".join(",".join(str(gpu_id) for gpu_id in worker.gpus) for worker in spec.workers) or "none"

    tensor_parallel_values = [
        worker.tensor_parallel_size for worker in spec.workers if worker.tensor_parallel_size is not None
    ]
    if spec.worker_defaults.tensor_parallel_size is not None:
        tensor_parallel = str(spec.worker_defaults.tensor_parallel_size)
    elif not tensor_parallel_values:
        tensor_parallel = "auto"
    elif len(set(tensor_parallel_values)) == 1:
        tensor_parallel = str(tensor_parallel_values[0])
    else:
        tensor_parallel = "mixed"

    data_parallel_values = [
        worker.data_parallel_size for worker in spec.workers if worker.data_parallel_size is not None
    ]
    if spec.worker_defaults.data_parallel_size is not None:
        data_parallel = str(spec.worker_defaults.data_parallel_size)
    elif not data_parallel_values:
        data_parallel = "1"
    elif len(set(data_parallel_values)) == 1:
        data_parallel = str(data_parallel_values[0])
    else:
        data_parallel = "mixed"

    expert_parallel_values = [
        worker.expert_parallel_enabled for worker in spec.workers if worker.expert_parallel_enabled is not None
    ]
    if spec.worker_defaults.expert_parallel_enabled is not None:
        expert_parallel = "on" if spec.worker_defaults.expert_parallel_enabled else "off"
    elif not expert_parallel_values:
        expert_parallel = "off"
    elif all(expert_parallel_values):
        expert_parallel = "on"
    elif not any(expert_parallel_values):
        expert_parallel = "off"
    else:
        expert_parallel = "mixed"

    return (
        f"gpu_architecture={spec.gpu_architecture}, "
        f"workers={worker_groups}, "
        f"tp={tensor_parallel}, "
        f"dp={data_parallel}, "
        f"expert_parallel={expert_parallel}"
    )


def _parse_gpu_group_list(raw: str) -> list[tuple[int, ...]]:
    groups: list[tuple[int, ...]] = []
    seen: set[int] = set()
    for raw_group in raw.split(";"):
        group = raw_group.strip()
        if not group:
            continue
        gpu_ids: list[int] = []
        for raw_gpu in group.split(","):
            candidate = raw_gpu.strip()
            if not candidate or not candidate.isdigit():
                raise ValueError("GPU groups must use comma-separated integers, for example 0,1;2,3")
            gpu_id = int(candidate)
            if gpu_id in seen:
                raise ValueError(f"GPU {gpu_id} is listed more than once")
            seen.add(gpu_id)
            gpu_ids.append(gpu_id)
        if not gpu_ids:
            raise ValueError("Each worker must list at least one GPU")
        groups.append(tuple(gpu_ids))
    if not groups:
        raise ValueError("At least one worker GPU group is required")
    return groups


def _posix_path(path: str) -> str:
    return path.replace(os.sep, "/")
