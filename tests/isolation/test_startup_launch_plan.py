from pathlib import Path

import pytest

from utils.models import deployment as model_deployment
from utils.stack import launch as stack_launch
from utils.stack import schema as env_schema
from utils.stack import startup as start_utils


pytestmark = [pytest.mark.isolation, pytest.mark.chatbot_provider, pytest.mark.batch_client]

ROOT = Path(__file__).resolve().parents[2]


def _schema():
    return env_schema.load_env_schema(str(ROOT), strict=True)


def _gpu(index: int) -> model_deployment.LocalGpuInfo:
    return model_deployment.LocalGpuInfo(
        index=index,
        name=f"GPU-{index}",
        gpu_architecture="nvidia_ampere",
    )


def _write_deployment(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _resolve_deployment(
    tmp_path: Path,
    *,
    role: str,
    model_family: str,
    worker_gpu_groups: list[list[int]],
    config_var: str,
    generated_name: str,
):
    lines = [
        'api_version = "ukbgpt/v1alpha1"',
        'kind = "model_deployment"',
        f'role = "{role}"',
        f'model_family = "{model_family}"',
        'gpu_architecture = "auto"',
        'router = "auto"',
        "",
    ]
    for gpu_group in worker_gpu_groups:
        gpu_values = ", ".join(str(gpu_id) for gpu_id in gpu_group)
        lines.extend(
            [
                "[[workers]]",
                f"gpus = [{gpu_values}]",
                "",
            ]
        )

    inventory = {gpu_id: _gpu(gpu_id) for group in worker_gpu_groups for gpu_id in group}
    deployment_path = _write_deployment(tmp_path / f"{generated_name}.toml", lines)
    return model_deployment.resolve_model_deployment(
        schema=_schema(),
        config_path=str(deployment_path),
        config_var=config_var,
        runtime_mode="chatbot_provider",
        generated_compose_path=str(tmp_path / f"{generated_name}.yml"),
        gpu_inventory=inventory,
    )


def _empty_discovery() -> stack_launch.BackendDiscovery:
    empty = stack_launch.BackendRoleDiscovery()
    return stack_launch.BackendDiscovery(
        llm=empty,
        embedding=empty,
        stt=empty,
        tts=empty,
    )


def test_plan_service_startup_leaves_non_overlapping_workers_parallel(tmp_path):
    llm = _resolve_deployment(
        tmp_path,
        role="llm",
        model_family="model.llm.qwen_qwen3_5_0_8b",
        worker_gpu_groups=[[0]],
        config_var="MODEL_DEPLOYMENT_CONFIG",
        generated_name="llm",
    )
    embedding = _resolve_deployment(
        tmp_path,
        role="embedding",
        model_family="model.embedding.qwen_qwen3_embedding_4b",
        worker_gpu_groups=[[1]],
        config_var="EMBEDDING_MODEL_DEPLOYMENT_CONFIG",
        generated_name="embedding",
    )
    stt = _resolve_deployment(
        tmp_path,
        role="stt",
        model_family="model.stt.mistralai_voxtral_mini_4b_realtime_2602",
        worker_gpu_groups=[[2]],
        config_var="STT_MODEL_DEPLOYMENT_CONFIG",
        generated_name="stt",
    )

    discovery = _empty_discovery()
    discovery = stack_launch.BackendDiscovery(
        llm=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("llm", 0),),
        ),
        embedding=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("embedding", 0),),
        ),
        stt=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("stt", 0),),
        ),
        tts=discovery.tts,
    )

    plan = start_utils.plan_service_startup(
        ["frontend", "ingress"],
        discovery,
        (llm, embedding, stt),
    )

    assert list(plan.core_services) == ["frontend", "ingress"]
    assert [target.service_name for target in plan.uncontended_workers] == [
        "stt_worker_0",
        "embedding_worker_0",
        "worker_0",
    ]
    assert plan.shared_gpu_worker_phases == ()
    assert plan.routers == ()


def test_plan_service_startup_serializes_only_shared_gpu_workers(tmp_path):
    llm = _resolve_deployment(
        tmp_path,
        role="llm",
        model_family="model.llm.qwen_qwen3_5_0_8b",
        worker_gpu_groups=[[0]],
        config_var="MODEL_DEPLOYMENT_CONFIG",
        generated_name="llm",
    )
    embedding = _resolve_deployment(
        tmp_path,
        role="embedding",
        model_family="model.embedding.qwen_qwen3_embedding_4b",
        worker_gpu_groups=[[0]],
        config_var="EMBEDDING_MODEL_DEPLOYMENT_CONFIG",
        generated_name="embedding",
    )
    stt = _resolve_deployment(
        tmp_path,
        role="stt",
        model_family="model.stt.mistralai_voxtral_mini_4b_realtime_2602",
        worker_gpu_groups=[[2]],
        config_var="STT_MODEL_DEPLOYMENT_CONFIG",
        generated_name="stt",
    )

    discovery = _empty_discovery()
    discovery = stack_launch.BackendDiscovery(
        llm=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("llm", 0),),
        ),
        embedding=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("embedding", 0),),
        ),
        stt=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("stt", 0),),
        ),
        tts=discovery.tts,
    )

    plan = start_utils.plan_service_startup(
        ["frontend", "ingress"],
        discovery,
        (llm, embedding, stt),
    )

    assert [target.service_name for target in plan.uncontended_workers] == ["stt_worker_0"]
    assert [
        [target.service_name for target in phase]
        for phase in plan.shared_gpu_worker_phases
    ] == [["embedding_worker_0"], ["worker_0"]]


def test_plan_service_startup_advances_independent_shared_gpu_groups_in_parallel_rounds(tmp_path):
    llm = _resolve_deployment(
        tmp_path,
        role="llm",
        model_family="model.llm.qwen_qwen3_5_0_8b",
        worker_gpu_groups=[[0]],
        config_var="MODEL_DEPLOYMENT_CONFIG",
        generated_name="llm",
    )
    embedding = _resolve_deployment(
        tmp_path,
        role="embedding",
        model_family="model.embedding.qwen_qwen3_embedding_4b",
        worker_gpu_groups=[[0]],
        config_var="EMBEDDING_MODEL_DEPLOYMENT_CONFIG",
        generated_name="embedding",
    )
    stt = _resolve_deployment(
        tmp_path,
        role="stt",
        model_family="model.stt.mistralai_voxtral_mini_4b_realtime_2602",
        worker_gpu_groups=[[7]],
        config_var="STT_MODEL_DEPLOYMENT_CONFIG",
        generated_name="stt",
    )
    tts = _resolve_deployment(
        tmp_path,
        role="tts",
        model_family="model.tts.mistralai_voxtral_4b_tts_2603",
        worker_gpu_groups=[[7]],
        config_var="TTS_MODEL_DEPLOYMENT_CONFIG",
        generated_name="tts",
    )

    discovery = _empty_discovery()
    discovery = stack_launch.BackendDiscovery(
        llm=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("llm", 0),),
        ),
        embedding=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("embedding", 0),),
        ),
        stt=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("stt", 0),),
        ),
        tts=stack_launch.BackendRoleDiscovery(
            workers=(model_deployment.worker_service_name("tts", 0),),
        ),
    )

    plan = start_utils.plan_service_startup(
        ["frontend", "ingress"],
        discovery,
        (llm, embedding, stt, tts),
    )

    assert plan.uncontended_workers == ()
    assert [
        [target.service_name for target in phase]
        for phase in plan.shared_gpu_worker_phases
    ] == [
        ["stt_worker_0", "embedding_worker_0"],
        ["tts_worker_0", "worker_0"],
    ]


def test_plan_service_startup_defers_router_until_after_role_workers(tmp_path):
    llm = _resolve_deployment(
        tmp_path,
        role="llm",
        model_family="model.llm.qwen_qwen3_5_0_8b",
        worker_gpu_groups=[[0], [1]],
        config_var="MODEL_DEPLOYMENT_CONFIG",
        generated_name="llm",
    )

    discovery = _empty_discovery()
    discovery = stack_launch.BackendDiscovery(
        llm=stack_launch.BackendRoleDiscovery(
            workers=(
                model_deployment.worker_service_name("llm", 0),
                model_deployment.worker_service_name("llm", 1),
            ),
            router_services=(model_deployment.router_service_name("llm"),),
        ),
        embedding=discovery.embedding,
        stt=discovery.stt,
        tts=discovery.tts,
    )

    plan = start_utils.plan_service_startup(
        ["frontend", "ingress"],
        discovery,
        (llm,),
    )

    assert [target.service_name for target in plan.uncontended_workers] == [
        "worker_0",
        "worker_1",
    ]
    assert plan.shared_gpu_worker_phases == ()
    assert [target.service_name for target in plan.routers] == ["backend_router"]
