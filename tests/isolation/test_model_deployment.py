from pathlib import Path

import pytest

from utils.models import deployment as model_deployment
from utils.stack import schema as env_schema


pytestmark = [pytest.mark.isolation, pytest.mark.chatbot_provider, pytest.mark.batch_client]

ROOT = Path(__file__).resolve().parents[2]


def _schema():
    return env_schema.load_env_schema(str(ROOT), strict=True)


def _gpu(index: int, name: str, gpu_architecture: str) -> model_deployment.LocalGpuInfo:
    return model_deployment.LocalGpuInfo(
        index=index,
        name=name,
        gpu_architecture=gpu_architecture,
    )


def _write_deployment(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_resolve_model_deployment_rejects_unknown_model_family(tmp_path):
    schema = _schema()
    deployment_path = _write_deployment(
        tmp_path / "unknown.toml",
        [
            'api_version = "ukbgpt/v1alpha1"',
            'kind = "model_deployment"',
            'role = "llm"',
            'model_family = "model.llm.does_not_exist"',
            'gpu_architecture = "auto"',
            'router = "auto"',
            "",
            "[[workers]]",
            "gpus = [0]",
        ],
    )

    with pytest.raises(ValueError, match="unknown model_family"):
        model_deployment.resolve_model_deployment(
            schema=schema,
            config_path=str(deployment_path),
            config_var="MODEL_DEPLOYMENT_CONFIG",
            runtime_mode="chatbot_provider",
            generated_compose_path=str(tmp_path / "model.llm.yml"),
            gpu_inventory={0: _gpu(0, "A100", "nvidia_ampere")},
        )


def test_resolve_model_deployment_rejects_duplicate_gpu_assignments(tmp_path):
    schema = _schema()
    deployment_path = _write_deployment(
        tmp_path / "duplicate-gpu.toml",
        [
            'api_version = "ukbgpt/v1alpha1"',
            'kind = "model_deployment"',
            'role = "llm"',
            'model_family = "model.llm.openai_gpt_oss_120b"',
            'gpu_architecture = "auto"',
            'router = "auto"',
            "",
            "[[workers]]",
            "gpus = [0, 1]",
            "",
            "[[workers]]",
            "gpus = [1, 2]",
        ],
    )

    with pytest.raises(ValueError, match="multiple workers"):
        model_deployment.resolve_model_deployment(
            schema=schema,
            config_path=str(deployment_path),
            config_var="MODEL_DEPLOYMENT_CONFIG",
            runtime_mode="chatbot_provider",
            generated_compose_path=str(tmp_path / "model.llm.yml"),
            gpu_inventory={
                0: _gpu(0, "A100", "nvidia_ampere"),
                1: _gpu(1, "A100", "nvidia_ampere"),
                2: _gpu(2, "A100", "nvidia_ampere"),
            },
        )


def test_resolve_model_deployment_rejects_unknown_gpu_index(tmp_path):
    schema = _schema()
    deployment_path = ROOT / "tests" / "model_deployments" / "qwen-single.toml"

    with pytest.raises(ValueError, match="unavailable GPU index 0"):
        model_deployment.resolve_model_deployment(
            schema=schema,
            config_path=str(deployment_path),
            config_var="MODEL_DEPLOYMENT_CONFIG",
            runtime_mode="chatbot_provider",
            generated_compose_path=str(tmp_path / "model.llm.yml"),
            gpu_inventory={1: _gpu(1, "A100", "nvidia_ampere")},
        )


def test_resolve_model_deployment_rejects_mixed_architecture_gpu_selection(tmp_path):
    schema = _schema()
    deployment_path = _write_deployment(
        tmp_path / "mixed-arch.toml",
        [
            'api_version = "ukbgpt/v1alpha1"',
            'kind = "model_deployment"',
            'role = "llm"',
            'model_family = "model.llm.openai_gpt_oss_120b"',
            'gpu_architecture = "auto"',
            'router = "auto"',
            "",
            "[[workers]]",
            "gpus = [0, 1]",
        ],
    )

    with pytest.raises(ValueError, match="multiple GPU architectures"):
        model_deployment.resolve_model_deployment(
            schema=schema,
            config_path=str(deployment_path),
            config_var="MODEL_DEPLOYMENT_CONFIG",
            runtime_mode="chatbot_provider",
            generated_compose_path=str(tmp_path / "model.llm.yml"),
            gpu_inventory={
                0: _gpu(0, "A100", "nvidia_ampere"),
                1: _gpu(1, "RTX 6000 Pro Blackwell", "nvidia_blackwell"),
            },
        )


def test_resolve_model_deployment_rejects_tensor_parallel_exceeds_gpu_count(tmp_path):
    schema = _schema()
    deployment_path = _write_deployment(
        tmp_path / "invalid-tp.toml",
        [
            'api_version = "ukbgpt/v1alpha1"',
            'kind = "model_deployment"',
            'role = "llm"',
            'model_family = "model.llm.openai_gpt_oss_120b"',
            'gpu_architecture = "auto"',
            'router = "auto"',
            "",
            "[worker_defaults]",
            "tensor_parallel_size = 3",
            "expert_parallel_enabled = true",
            "",
            "[[workers]]",
            "gpus = [0, 1]",
        ],
    )

    with pytest.raises(ValueError, match="tensor_parallel_size"):
        model_deployment.resolve_model_deployment(
            schema=schema,
            config_path=str(deployment_path),
            config_var="MODEL_DEPLOYMENT_CONFIG",
            runtime_mode="chatbot_provider",
            generated_compose_path=str(tmp_path / "model.llm.yml"),
            gpu_inventory={
                0: _gpu(0, "A100", "nvidia_ampere"),
                1: _gpu(1, "A100", "nvidia_ampere"),
            },
        )


def test_resolve_model_deployment_rejects_unsupported_expert_parallel(tmp_path):
    schema = _schema()
    deployment_path = _write_deployment(
        tmp_path / "unsupported-ep.toml",
        [
            'api_version = "ukbgpt/v1alpha1"',
            'kind = "model_deployment"',
            'role = "llm"',
            'model_family = "model.llm.qwen_qwen3_5_0_8b"',
            'gpu_architecture = "auto"',
            'router = "auto"',
            "",
            "[worker_defaults]",
            "expert_parallel_enabled = true",
            "",
            "[[workers]]",
            "gpus = [0]",
        ],
    )

    with pytest.raises(ValueError, match="does not support it"):
        model_deployment.resolve_model_deployment(
            schema=schema,
            config_path=str(deployment_path),
            config_var="MODEL_DEPLOYMENT_CONFIG",
            runtime_mode="chatbot_provider",
            generated_compose_path=str(tmp_path / "model.llm.yml"),
            gpu_inventory={0: _gpu(0, "A100", "nvidia_ampere")},
        )


def test_render_model_compose_qwen_single_gpu(tmp_path):
    schema = _schema()
    resolved = model_deployment.resolve_model_deployment(
        schema=schema,
        config_path=str(ROOT / "tests" / "model_deployments" / "qwen-single.toml"),
        config_var="MODEL_DEPLOYMENT_CONFIG",
        runtime_mode="chatbot_provider",
        generated_compose_path=str(tmp_path / "model.llm.yml"),
        gpu_inventory={0: _gpu(0, "A100", "nvidia_ampere")},
    )

    rendered = model_deployment.render_model_compose(resolved, output_path=str(tmp_path / "model.llm.yml"))
    worker = rendered["services"]["worker_0"]
    assert "backend_router" not in rendered["services"]
    assert worker["deploy"]["resources"]["reservations"]["devices"][0]["device_ids"] == ["0"]
    assert "--scheduling-policy=priority" in worker["command"]
    assert "--tensor-parallel-size=1" in worker["command"]
    assert Path(worker["extends"]["file"]).resolve() == (
        ROOT / "compose" / "models" / "llm" / "qwen--qwen3.5-0.8b" / "base.yml"
    )


def test_list_wizard_model_deployments_filters_to_matching_family(tmp_path):
    schema = _schema()
    family = schema.model_families["model.llm.qwen_qwen3_5_0_8b"]
    managed_dir = model_deployment.wizard_model_deployment_dir(
        tmp_path,
        role="llm",
        family=family,
    )
    managed_dir.mkdir(parents=True, exist_ok=True)

    _write_deployment(
        managed_dir / "deployment-01.toml",
        [
            'api_version = "ukbgpt/v1alpha1"',
            'kind = "model_deployment"',
            'role = "llm"',
            'model_family = "model.llm.qwen_qwen3_5_0_8b"',
            'gpu_architecture = "auto"',
            'router = "auto"',
            "",
            "[[workers]]",
            "gpus = [0]",
        ],
    )
    _write_deployment(
        managed_dir / "deployment-02.toml",
        [
            'api_version = "ukbgpt/v1alpha1"',
            'kind = "model_deployment"',
            'role = "llm"',
            'model_family = "model.llm.openai_gpt_oss_120b"',
            'gpu_architecture = "auto"',
            'router = "auto"',
            "",
            "[[workers]]",
            "gpus = [0, 1]",
        ],
    )
    (managed_dir / "invalid.toml").write_text("not a deployment config\n", encoding="utf-8")

    discovered = model_deployment.list_wizard_model_deployments(
        tmp_path,
        role="llm",
        family=family,
    )

    assert [Path(option.path).name for option in discovered] == ["deployment-01.toml"]
    assert discovered[0].env_value == "compose/generated/deployments/llm/qwen_qwen3_5_0_8b/deployment-01.toml"


def test_render_model_compose_gpt_oss_two_workers_chatbot_provider(tmp_path):
    schema = _schema()
    resolved = model_deployment.resolve_model_deployment(
        schema=schema,
        config_path=str(ROOT / "tests" / "model_deployments" / "gpt-oss-2x2.toml"),
        config_var="MODEL_DEPLOYMENT_CONFIG",
        runtime_mode="chatbot_provider",
        generated_compose_path=str(tmp_path / "model.llm.yml"),
        gpu_inventory={
            0: _gpu(0, "A100", "nvidia_ampere"),
            1: _gpu(1, "A100", "nvidia_ampere"),
            2: _gpu(2, "A100", "nvidia_ampere"),
            3: _gpu(3, "A100", "nvidia_ampere"),
        },
    )

    rendered = model_deployment.render_model_compose(resolved, output_path=str(tmp_path / "model.llm.yml"))
    assert "backend_router" in rendered["services"]
    assert rendered["services"]["worker_0"]["environment"]["VLLM_ATTENTION_BACKEND"] == "TRITON_ATTN"
    assert "--scheduling-policy=priority" in rendered["services"]["worker_0"]["command"]
    assert "--enable-expert-parallel" in rendered["services"]["worker_0"]["command"]
    assert "--tensor-parallel-size=2" in rendered["services"]["worker_0"]["command"]
    assert "--tensor-parallel-size=2" in rendered["services"]["worker_1"]["command"]
    assert rendered["services"]["backend_router"]["environment"]["BACKEND_NODES"] == "worker_0:5000,worker_1:5000"
    assert rendered["services"]["backend_router"]["environment"]["CHECK_URL"] == "http://127.0.0.1:5000/health"


def test_router_auto_disabled_in_batch_mode(tmp_path):
    schema = _schema()
    resolved = model_deployment.resolve_model_deployment(
        schema=schema,
        config_path=str(ROOT / "tests" / "model_deployments" / "gpt-oss-2x2.toml"),
        config_var="MODEL_DEPLOYMENT_CONFIG",
        runtime_mode="batch_client",
        generated_compose_path=str(tmp_path / "model.llm.yml"),
        gpu_inventory={
            0: _gpu(0, "A100", "nvidia_ampere"),
            1: _gpu(1, "A100", "nvidia_ampere"),
            2: _gpu(2, "A100", "nvidia_ampere"),
            3: _gpu(3, "A100", "nvidia_ampere"),
        },
    )

    assert resolved.router_enabled is False
    rendered = model_deployment.render_model_compose(resolved, output_path=str(tmp_path / "model.llm.yml"))
    assert "backend_router" not in rendered["services"]
