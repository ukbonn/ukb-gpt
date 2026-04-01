import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .schema import EnvSchema, SelectionContext, load_env_schema, ref_is_applicable, ref_is_required, resolve_effective_variable
from ..models.deployment import (
    ResolvedModelDeployment,
    render_model_compose,
    resolve_model_deployment,
    write_rendered_compose,
)

DEFAULT_LLM_DEPLOYMENT_CONFIG = "examples/model_deployments/gpt-oss-120b.1x8.toml"
TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DeploymentRuntimeBundle:
    compose_args: list[str]
    llm_deployment_config: str
    embedding_deployment_config: str
    stt_deployment_config: str
    tts_deployment_config: str
    llm_compose_flags: list[str]
    embedding_compose_flags: list[str]
    stt_compose_flags: list[str]
    tts_compose_flags: list[str]
    resolved_deployments: tuple[ResolvedModelDeployment, ...]


def resolve_model_deployment_config_path(
    deployment_config: str,
    var_name: str = "MODEL_DEPLOYMENT_CONFIG",
    *,
    root_dir: str | Path,
) -> str:
    if os.path.isfile(deployment_config):
        return os.path.realpath(deployment_config)

    relative_candidate = os.path.join(str(root_dir), deployment_config)
    if os.path.isfile(relative_candidate):
        return os.path.realpath(relative_candidate)

    print(f"❌ Error: Model deployment config not found for {var_name}: {deployment_config}")
    print("   Checked: direct path and <repo-root>/<value>.")
    print(
        "   Action: set "
        f"{var_name} to an explicit path (for example: examples/model_deployments/gpt-oss-120b.2x2.toml)."
    )
    sys.exit(1)


def resolve_optional_model_deployment_config(
    var_name: str,
    *,
    root_dir: str | Path,
    default: str = "",
) -> str:
    raw = os.getenv(var_name)
    if raw is None:
        raw = default

    value = (raw or "").strip()
    if value.lower() in {"", "none", "off", "disable", "disabled"}:
        return ""

    return resolve_model_deployment_config_path(value, var_name, root_dir=root_dir)


def resolve_selected_model_deployments(
    llm_deployment_config: str,
    embedding_deployment_config: str,
    stt_deployment_config: str,
    tts_deployment_config: str,
    *,
    runtime_mode: str,
    root_dir: str | Path,
) -> tuple[ResolvedModelDeployment, ...]:
    schema = load_env_schema(str(root_dir), strict=True)
    generated_dir = os.path.join(str(root_dir), "compose", "generated")
    selected: list[ResolvedModelDeployment] = []
    for role, var_name, config_path in (
        ("llm", "MODEL_DEPLOYMENT_CONFIG", llm_deployment_config),
        ("embedding", "EMBEDDING_MODEL_DEPLOYMENT_CONFIG", embedding_deployment_config),
        ("stt", "STT_MODEL_DEPLOYMENT_CONFIG", stt_deployment_config),
        ("tts", "TTS_MODEL_DEPLOYMENT_CONFIG", tts_deployment_config),
    ):
        if not config_path:
            continue
        generated_path = os.path.join(generated_dir, f"model.{role}.yml")
        try:
            selected.append(
                resolve_model_deployment(
                    schema=schema,
                    config_path=config_path,
                    config_var=var_name,
                    runtime_mode=runtime_mode,
                    generated_compose_path=generated_path,
                )
            )
        except ValueError as exc:
            print(f"❌ Error: {exc}")
            sys.exit(1)
    return tuple(selected)


def validate_model_specific_requirements(
    resolved_deployments: tuple[ResolvedModelDeployment, ...],
    *,
    root_dir: str | Path,
) -> None:
    schema = load_env_schema(str(root_dir), strict=True)
    context = _selection_context_from_current_env(schema)
    missing_or_invalid = False

    for deployment in resolved_deployments:
        family = deployment.family
        for ref in family.variables:
            if not ref_is_applicable(ref, context=context, env=os.environ):
                continue
            if not ref_is_required(ref, context=context, env=os.environ):
                continue

            resolved = resolve_effective_variable(schema, ref)
            value = _env_str(ref.var_id)
            if not value:
                print(
                    f"❌ Error: {ref.var_id} is required by selected model family "
                    f"{family.title} ({deployment.resolved_gpu_architecture})."
                )
                if resolved.description:
                    print(f"   {resolved.description}")
                missing_or_invalid = True
                continue

            validators = set(resolved.spec.validators)
            if "directory" in validators and not os.path.isdir(value):
                print(
                    f"❌ Error: {ref.var_id} must be an existing directory for model family "
                    f"{family.title}: {value}"
                )
                missing_or_invalid = True
                continue
            if "file" in validators and not os.path.isfile(value):
                print(
                    f"❌ Error: {ref.var_id} must be an existing file for model family "
                    f"{family.title}: {value}"
                )
                missing_or_invalid = True
                continue

            if ref.var_id == "GPT_OSS_ENCODINGS_PATH":
                print(f"🔒 Info: gpt-oss Harmony encodings mount enabled: {value}")

    if missing_or_invalid:
        sys.exit(1)


def configure_vllm_worker_images(
    resolved_deployments: tuple[ResolvedModelDeployment, ...],
) -> None:
    for deployment in resolved_deployments:
        if "VLLM_OPENAI_IMAGE" not in deployment.family.runtime.build_args:
            continue

        env_var = _vllm_openai_image_var_for_role(deployment.role)
        image, source = _resolve_vllm_openai_image(
            env_var,
            family_default=deployment.family.runtime.default_vllm_openai_image,
        )
        if not image:
            print(f"❌ Error: {deployment.family.title} has no worker image configured.")
            print(
                f"   Action: export {env_var}=\"vllm/vllm-openai:<tag>\" "
                "or set VLLM_OPENAI_IMAGE as a shared fallback."
            )
            sys.exit(1)

        os.environ[env_var] = image
        print(
            f"🐳 [Image] {_deployment_label(deployment.role)} worker image: "
            f"{image} (source: {source})"
        )


def prepare_model_deployments(
    *,
    root_dir: str | Path,
    runtime_mode: str,
) -> DeploymentRuntimeBundle:
    llm_deployment_config = resolve_optional_model_deployment_config(
        "MODEL_DEPLOYMENT_CONFIG",
        root_dir=root_dir,
        default=DEFAULT_LLM_DEPLOYMENT_CONFIG,
    )
    embedding_deployment_config = resolve_optional_model_deployment_config(
        "EMBEDDING_MODEL_DEPLOYMENT_CONFIG",
        root_dir=root_dir,
    )
    stt_deployment_config = resolve_optional_model_deployment_config(
        "STT_MODEL_DEPLOYMENT_CONFIG",
        root_dir=root_dir,
    )
    tts_deployment_config = resolve_optional_model_deployment_config(
        "TTS_MODEL_DEPLOYMENT_CONFIG",
        root_dir=root_dir,
    )

    for label, config in (
        ("LLM", llm_deployment_config),
        ("Embedding", embedding_deployment_config),
        ("STT", stt_deployment_config),
        ("TTS", tts_deployment_config),
    ):
        print(f"🧠 [Model] {label} configuration: {config or 'DISABLED'}")

    if (
        not llm_deployment_config
        and not embedding_deployment_config
        and not stt_deployment_config
        and not tts_deployment_config
    ):
        print("❌ Error: No backend model deployment selected.")
        print(
            "   Action: set MODEL_DEPLOYMENT_CONFIG and/or "
            "EMBEDDING_MODEL_DEPLOYMENT_CONFIG and/or STT_MODEL_DEPLOYMENT_CONFIG "
            "and/or TTS_MODEL_DEPLOYMENT_CONFIG."
        )
        sys.exit(1)

    resolved_deployments = resolve_selected_model_deployments(
        llm_deployment_config=llm_deployment_config,
        embedding_deployment_config=embedding_deployment_config,
        stt_deployment_config=stt_deployment_config,
        tts_deployment_config=tts_deployment_config,
        runtime_mode=runtime_mode,
        root_dir=root_dir,
    )
    configure_vllm_worker_images(resolved_deployments)
    validate_model_specific_requirements(
        resolved_deployments,
        root_dir=root_dir,
    )

    model_base_compose = os.path.join(str(root_dir), "compose", "model.base.yml")
    if not os.path.isfile(model_base_compose):
        print(f"❌ Error: Required compose file missing: {model_base_compose}")
        sys.exit(1)

    compose_args = ["-f", model_base_compose]
    rendered_paths = _render_resolved_model_deployments(
        resolved_deployments,
        root_dir=root_dir,
    )
    llm_compose_flags: list[str] = []
    embedding_compose_flags: list[str] = []
    stt_compose_flags: list[str] = []
    tts_compose_flags: list[str] = []

    llm_generated_compose = rendered_paths.get("llm", "")
    embedding_generated_compose = rendered_paths.get("embedding", "")
    stt_generated_compose = rendered_paths.get("stt", "")
    tts_generated_compose = rendered_paths.get("tts", "")

    if llm_generated_compose:
        llm_compose_flags = ["-f", model_base_compose, "-f", llm_generated_compose]
        compose_args += ["-f", llm_generated_compose]

    if embedding_generated_compose:
        embedding_compose_flags = ["-f", model_base_compose, "-f", embedding_generated_compose]
        compose_args += ["-f", embedding_generated_compose]

    if stt_generated_compose:
        stt_compose_flags = ["-f", model_base_compose, "-f", stt_generated_compose]
        compose_args += ["-f", stt_generated_compose]

    if tts_generated_compose:
        tts_compose_flags = ["-f", model_base_compose, "-f", tts_generated_compose]
        compose_args += ["-f", tts_generated_compose]

    return DeploymentRuntimeBundle(
        compose_args=compose_args,
        llm_deployment_config=llm_deployment_config,
        embedding_deployment_config=embedding_deployment_config,
        stt_deployment_config=stt_deployment_config,
        tts_deployment_config=tts_deployment_config,
        llm_compose_flags=llm_compose_flags,
        embedding_compose_flags=embedding_compose_flags,
        stt_compose_flags=stt_compose_flags,
        tts_compose_flags=tts_compose_flags,
        resolved_deployments=resolved_deployments,
    )


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


def _selection_context_from_current_env(schema: EnvSchema) -> SelectionContext:
    mode_name = "batch_client" if _env_bool("BATCH_CLIENT_MODE_ON") else "chatbot_provider"
    enabled_features = {
        overlay.name
        for overlay in schema.by_kind("feature")
        if overlay.toggle_var and _env_bool(overlay.toggle_var)
    }
    enabled_apps = {
        overlay.name
        for overlay in schema.by_kind("app")
        if overlay.toggle_var and _env_bool(overlay.toggle_var)
    }
    return SelectionContext(
        mode=mode_name,
        enabled_features=frozenset(enabled_features),
        enabled_apps=frozenset(enabled_apps),
    )


def _resolve_vllm_openai_image(
    var_name: str,
    *,
    family_default: str = "",
) -> tuple[str, str]:
    selected = _env_str(var_name)
    if selected:
        return selected, var_name

    global_image = _env_str("VLLM_OPENAI_IMAGE")
    if global_image:
        return global_image, "VLLM_OPENAI_IMAGE"

    if family_default:
        return family_default, "model family default"

    return "", ""


def _deployment_label(role: str) -> str:
    return {"llm": "LLM", "embedding": "Embedding", "stt": "STT", "tts": "TTS"}.get(role, role)


def _vllm_openai_image_var_for_role(role: str) -> str:
    return {
        "llm": "VLLM_OPENAI_IMAGE_LLM",
        "embedding": "VLLM_OPENAI_IMAGE_EMBEDDING",
        "stt": "VLLM_OPENAI_IMAGE_STT",
        "tts": "VLLM_OPENAI_IMAGE_TTS",
    }.get(role, "VLLM_OPENAI_IMAGE")


def _cleanup_generated_model_compose_files(active_paths: set[str], *, root_dir: str | Path) -> None:
    generated_dir = Path(root_dir) / "compose" / "generated"
    if not generated_dir.is_dir():
        return
    for candidate in generated_dir.glob("model.*.yml"):
        if str(candidate.resolve()) not in active_paths:
            candidate.unlink(missing_ok=True)


def _render_resolved_model_deployments(
    resolved_deployments: tuple[ResolvedModelDeployment, ...],
    *,
    root_dir: str | Path,
) -> dict[str, str]:
    rendered_paths: dict[str, str] = {}
    active_paths: set[str] = set()
    for deployment in resolved_deployments:
        compose_data = render_model_compose(
            deployment,
            output_path=deployment.generated_compose_path,
        )
        write_rendered_compose(deployment.generated_compose_path, compose_data)
        resolved_path = str(Path(deployment.generated_compose_path).resolve())
        active_paths.add(resolved_path)
        rendered_paths[deployment.role] = resolved_path
    _cleanup_generated_model_compose_files(active_paths, root_dir=root_dir)
    return rendered_paths
