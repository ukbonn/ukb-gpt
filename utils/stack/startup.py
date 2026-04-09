import os
import sys
import datetime
import subprocess
import ipaddress
import re
import shlex
import stat
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from .deployments import (
    DeploymentRuntimeBundle,
    prepare_model_deployments,
    resolve_model_deployment_config_path,
)
from .launch import (
    BackendDiscovery,
    BackendRoleDiscovery,
    StartupLaunchPlan,
    plan_service_startup,
    start_services as start_launch_services,
)
from .schema import (
    EnvSchema,
    ModelFamilySpec,
    OverlaySpec,
    SelectionContext,
    load_env_schema,
    parse_bool as schema_parse_bool,
    ref_is_applicable,
    ref_is_required,
    resolve_effective_variable,
    selected_overlays as schema_selected_overlays,
)
from ..models.deployment import (
    WizardModelDeploymentOption,
    create_wizard_model_deployment,
    list_wizard_model_deployments,
    model_deployment_summary,
    parse_model_deployment,
)

# Define the root directory of the project.
ROOT_DIR = str(Path(__file__).resolve().parents[2])
TRUE_VALUES = {"1", "true", "yes", "on"}
RFC1918_PRIVATE_V4_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_MEBIBYTE = 1024 * 1024
_DATASET_STRUCTURING_DEFAULT_MEMORY_RATIO = 0.90
_DATASET_STRUCTURING_MIN_MEMORY_LIMIT_MIB = 512
_EXPECTED_CERT_MARKERS = (
    "-----BEGIN CERTIFICATE-----",
    "-----BEGIN TRUSTED CERTIFICATE-----",
)
_EXPECTED_KEY_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN ENCRYPTED PRIVATE KEY-----",
    "-----BEGIN DSA PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
)
_FULLCHAIN_CERT_COUNT_HINT = 2
RULE_WIDTH = 72
_ENV_FILE_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BANNER_ART = (
    "██╗   ██╗██╗  ██╗██████╗          ██████╗ ██████╗ ████████╗",
    "██║   ██║██║ ██╔╝██╔══██╗        ██╔════╝ ██╔══██╗╚══██╔══╝",
    "██║   ██║█████╔╝ ██████╔╝ █████╗ ██║  ███╗██████╔╝   ██║   ",
    "██║   ██║██╔═██╗ ██╔══██╗ ╚════╝ ██║   ██║██╔═══╝    ██║   ",
    "╚██████╔╝██║  ██╗██████╔╝        ╚██████╔╝██║        ██║   ",
    " ╚═════╝ ╚═╝  ╚═╝╚═════╝          ╚═════╝ ╚═╝        ╚═╝   ",
)


def _first_non_empty_non_comment_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        return stripped
    return ""


def _openssl_certificate_inspect(path: str) -> bool | None:
    openssl = shutil.which("openssl")
    if not openssl:
        return None

    proc = subprocess.run(
        [openssl, "x509", "-in", path, "-noout", "-subject", "-issuer"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0

def _count_pem_cert_blocks(contents: str) -> int:
    # Count explicit certificate PEM blocks to detect whether intermediates were included.
    return sum(contents.count(marker) for marker in _EXPECTED_CERT_MARKERS)


def _path_is_within_directory(path: str, directory: str) -> bool:
    try:
        candidate = Path(path).expanduser().resolve(strict=False)
        root = Path(directory).expanduser().resolve(strict=False)
    except OSError:
        return False
    return candidate == root or root in candidate.parents


_ENV_SCHEMA_CACHE: EnvSchema | None = None


@dataclass(frozen=True)
class StartupConfig:
    compose_args: list[str]
    deployment_bundle: DeploymentRuntimeBundle
    batch_mode: bool
    core_services: list[str]


@dataclass(frozen=True)
class SchemaRuntimeSelection:
    schema: EnvSchema
    context: SelectionContext
    overlays: list
    enabled_features: dict[str, bool]
    enabled_apps: dict[str, bool]


def _load_runtime_env_schema() -> EnvSchema:
    global _ENV_SCHEMA_CACHE
    if _ENV_SCHEMA_CACHE is None:
        _ENV_SCHEMA_CACHE = load_env_schema(ROOT_DIR, strict=True)
    return _ENV_SCHEMA_CACHE


def _schema_default(var_name: str, fallback: str = "") -> str:
    schema = _load_runtime_env_schema()
    spec = schema.catalog.get(var_name)
    if spec is None or spec.default is None:
        return fallback
    return spec.default


def _has_dir_write_access_for_identity(path: str, uid: int, gid: int) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False

    mode = st.st_mode
    if uid == 0:
        # Root still requires execute on a directory for traversal.
        return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))

    if uid == st.st_uid:
        return bool(mode & stat.S_IWUSR) and bool(mode & stat.S_IXUSR)
    if gid == st.st_gid:
        return bool(mode & stat.S_IWGRP) and bool(mode & stat.S_IXGRP)
    return bool(mode & stat.S_IWOTH) and bool(mode & stat.S_IXOTH)


def _chown_tree(path: str, uid: int, gid: int) -> None:
    for root, dirs, files in os.walk(path):
        os.chown(root, uid, gid)
        for name in dirs:
            os.chown(os.path.join(root, name), uid, gid, follow_symlinks=False)
        for name in files:
            os.chown(os.path.join(root, name), uid, gid, follow_symlinks=False)


def load_env_file_values(path: str | Path) -> dict[str, str]:
    env_path = Path(path).expanduser().resolve()
    if not env_path.is_file():
        raise ValueError(f"Env file does not exist: {env_path}")

    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_FILE_KEY_PATTERN.match(key):
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif len(value) >= 2 and value[0] == "'" and value[-1] == "'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        loaded[key] = value
    return loaded


def apply_env_file(path: str | Path, *, override: bool = False) -> dict[str, str]:
    loaded = load_env_file_values(path)
    for key, value in loaded.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def default_env_file_path(root_dir: str | None = None) -> str:
    base_dir = root_dir or ROOT_DIR
    return os.path.join(base_dir, ".env")


def _print_rule(char: str = "=", width: int = RULE_WIDTH) -> None:
    print(char * width)


def _print_banner(title: str, subtitle: str = "") -> None:
    print()
    _print_rule("=")
    print(title)
    if subtitle:
        print(subtitle)
    _print_rule("=")


def print_ukbgpt_banner(*info_lines: str) -> None:
    lines = [line for line in info_lines if line]
    width = max(
        RULE_WIDTH,
        *(len(line) for line in _BANNER_ART),
        *(len(line) for line in lines),
    )

    print()
    print(f"╔{'═' * (width + 2)}╗")
    for line in _BANNER_ART:
        print(f"║ {line.center(width)} ║")
    if lines:
        print(f"║ {' '.center(width)} ║")
        for line in lines:
            print(f"║ {line.center(width)} ║")
    print(f"╚{'═' * (width + 2)}╝")


def _print_section(title: str, subtitle: str = "") -> None:
    print()
    _print_rule("-")
    print(title)
    if subtitle:
        print(subtitle)
    _print_rule("-")


def _print_subsection(title: str, subtitle: str = "") -> None:
    print()
    print(title)
    _print_rule(".",)
    if subtitle:
        print(subtitle)


def _format_summary(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def _prompt_choice(prompt: str, options: list[str], default_index: int = 0) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw:
            return default_index
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        print(f"Please enter a number between 1 and {len(options)}.")


def _prompt_choice_required(prompt: str, options: list[str]) -> int:
    while True:
        raw = input(prompt).strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return idx
        print(f"Please enter a number between 1 and {len(options)}.")


def _prompt_yes_no(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{suffix}]: ").strip().lower()
        if raw == "":
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer with y or n.")


def _prompt_value(
    var_name: str,
    var_type: str,
    default_value: str,
    description: str,
    examples: tuple[str, ...],
) -> str:
    if description:
        print(f"\n{var_name}: {description}")
    if examples:
        print(f"Example: {examples[0]}")
    if var_type == "bool":
        default_bool = schema_parse_bool(default_value, False)
        selected = _prompt_yes_no(f"Set {var_name}", default_bool)
        return "true" if selected else "false"

    prompt = f"Set {var_name}"
    if default_value:
        prompt += f" [default: {default_value}]"
    prompt += ": "
    raw = input(prompt).strip()
    if raw:
        return raw
    return default_value


def _dotenv_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("\"", "\\\"")
    return f"\"{escaped}\""


def _write_env_file(
    target_path: Path,
    *,
    ordered_var_names: list[str],
    values: dict[str, str],
    descriptions: dict[str, str],
) -> None:
    lines: list[str] = []
    lines.append("# Generated by start.py wizard")
    lines.append(f"# Generated at {datetime.datetime.utcnow().isoformat()}Z")
    lines.append("# Non-secret variables only. Export secrets in the shell before start.py up.")
    lines.append("")

    for var_name in ordered_var_names:
        value = values.get(var_name, "")
        description = descriptions.get(var_name, "").strip()
        if description:
            lines.append(f"# {description}")
        lines.append(f"{var_name}={_dotenv_quote(value)}")
        lines.append("")

    target_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _resolve_secret_input(var_name: str) -> str:
    direct_value = os.getenv(var_name)
    file_var = f"{var_name}_FILE"
    file_value = os.getenv(file_var)

    if direct_value and direct_value.strip():
        if file_value and file_value.strip():
            print(f"❌ Error: both {var_name} and {file_var} are set.")
            print(f"   Action: unset one of them so startup uses a single secret source.")
            sys.exit(1)
        return direct_value.strip()

    if not file_value or not file_value.strip():
        return ""

    resolved_path = os.path.abspath(os.path.expanduser(file_value.strip()))
    if not os.path.isfile(resolved_path):
        print(f"❌ Error: {file_var} does not point to a readable file: {resolved_path}")
        sys.exit(1)

    try:
        loaded_value = Path(resolved_path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"❌ Error: Failed to read {file_var} at {resolved_path}: {exc}")
        sys.exit(1)

    if not loaded_value.strip():
        print(f"❌ Error: {file_var} is empty: {resolved_path}")
        sys.exit(1)

    os.environ[file_var] = resolved_path
    os.environ[var_name] = loaded_value.strip()
    print(f"🔒 Info: Loaded {var_name} from {file_var}.")
    return os.environ[var_name]


def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


def _mode_from_selection(batch_mode: bool) -> str:
    return "batch_client" if batch_mode else "chatbot_provider"


def _has_value(value: str | None) -> bool:
    return bool((value or "").strip())


def _model_role_label(role: str) -> str:
    return {"llm": "LLM", "embedding": "Embedding", "stt": "STT", "tts": "TTS"}.get(role, role)


def _flatten_model_families(
    schema: EnvSchema,
    role: str,
    *,
    wizard_only: bool = True,
) -> list[ModelFamilySpec]:
    return schema.model_families_by_role(role, wizard_only=wizard_only)


def _merge_wizard_model_deployment_options(
    prefilled_option: WizardModelDeploymentOption | None,
    discovered_options: list[WizardModelDeploymentOption],
) -> list[WizardModelDeploymentOption]:
    merged: list[WizardModelDeploymentOption] = []
    seen_paths: set[str] = set()
    for option in ([prefilled_option] if prefilled_option is not None else []) + discovered_options:
        resolved_path = os.path.realpath(option.path)
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)
        merged.append(option)
    return merged


def _choose_existing_model_deployment(
    role_label: str,
    options: list[WizardModelDeploymentOption],
    *,
    default_index: int = 0,
) -> str:
    print(f"\nAvailable saved {role_label} deployment configs:")
    for idx, option in enumerate(options, start=1):
        source_label = "current" if option.source == "prefill" else option.source
        print(f"{idx}. {option.env_value} [{source_label}] ({model_deployment_summary(option.spec)})")

    selected_idx = _prompt_choice(
        f"Deployment [default {default_index + 1}]: ",
        [option.env_value for option in options],
        default_index=default_index,
    )
    return options[selected_idx].env_value


def _select_model_deployment_for_role(
    schema: EnvSchema,
    *,
    values: dict[str, str],
    role: str,
    var_name: str,
    allow_disable: bool,
    root_dir: str | Path = ROOT_DIR,
) -> tuple[str, ModelFamilySpec | None]:
    current = (values.get(var_name, "") or "").strip()
    prefilled_option: WizardModelDeploymentOption | None = None
    prefilled_family: ModelFamilySpec | None = None
    if current:
        try:
            resolved_path = resolve_model_deployment_config_path(current, var_name, root_dir=root_dir)
            deployment = parse_model_deployment(resolved_path)
            family = schema.model_families.get(deployment.model_family)
            if family and family.role == role:
                prefilled_family = family
                prefilled_option = WizardModelDeploymentOption(
                    path=resolved_path,
                    env_value=current,
                    spec=deployment,
                    source="prefill",
                )
        except ValueError as exc:
            print(f"⚠️  [Wizard] Ignoring invalid prefilled {var_name}: {exc}")

    options = _flatten_model_families(schema, role, wizard_only=True)
    if not options:
        if allow_disable:
            print(f"No {_model_role_label(role)} model families are available. Disabling backend.")
            return "", None
        raise ValueError(f"No wizard-enabled model families available for role: {role}")

    if prefilled_family is not None:
        family = prefilled_family
        print(f"Using prefilled {_model_role_label(role)} deployment family: {family.title}")
    else:
        print(f"\nSelect {_model_role_label(role)} model family:")
        option_labels: list[str] = []
        for idx, family in enumerate(options, start=1):
            print(f"{idx}. {family.title} ({family.summary})")
            option_labels.append(family.family_id)

        if allow_disable:
            disable_index = len(options) + 1
            print(f"{disable_index}. Disable {_model_role_label(role)} backend")
            option_labels.append("__disable__")

        selection_idx = _prompt_choice_required("Profile: ", option_labels)
        selected = option_labels[selection_idx]
        if selected == "__disable__":
            return "", None

        family = options[selection_idx]

    existing_options = _merge_wizard_model_deployment_options(
        prefilled_option,
        list_wizard_model_deployments(root_dir, role=role, family=family),
    )
    if existing_options:
        print(f"\nSaved {_model_role_label(role)} deployment configs found for {family.title}.")
        print("1. Load an existing deployment config")
        print("2. Create a new deployment config")
        action_idx = _prompt_choice(
            "Selection [default 1]: ",
            ["load", "create"],
            default_index=0,
        )
        if action_idx == 0:
            default_option_index = 0
            if prefilled_option is not None:
                for idx, option in enumerate(existing_options):
                    if os.path.realpath(option.path) == os.path.realpath(prefilled_option.path):
                        default_option_index = idx
                        break
            return (
                _choose_existing_model_deployment(
                    _model_role_label(role),
                    existing_options,
                    default_index=default_option_index,
                ),
                family,
            )
    else:
        print(f"No saved {_model_role_label(role)} deployment configs found for {family.title}.")
        print("Creating a new deployment config.")

    deployment_value = create_wizard_model_deployment(
        family=family,
        role=role,
        role_label=_model_role_label(role),
        root_dir=root_dir,
        prompt_choice=_prompt_choice,
        prompt_yes_no=_prompt_yes_no,
    )
    return deployment_value, family


def _merge_variable_refs(refs) -> list:
    by_var = {}
    for ref in refs:
        current = by_var.get(ref.var_id)
        if current is None or ref.prompt_order < current.prompt_order:
            by_var[ref.var_id] = ref
    return sorted(by_var.values(), key=lambda item: (item.prompt_order, item.var_id))


def _apply_variable_refs(
    schema: EnvSchema,
    refs,
    *,
    values: dict[str, str],
    context: SelectionContext,
    descriptions: dict[str, str],
    ordered_output_vars: list[str],
    secret_required: list[str],
    prompt_default_overrides: dict[str, str] | None = None,
) -> None:
    prompt_default_overrides = prompt_default_overrides or {}
    for ref in refs:
        if not ref_is_applicable(ref, context=context, env=values):
            continue

        resolved = resolve_effective_variable(schema, ref)
        spec = resolved.spec
        descriptions[ref.var_id] = resolved.description
        required = ref_is_required(ref, context=context, env=values)
        current_value = values.get(ref.var_id, "")
        has_current_value = _has_value(current_value)
        default_value = current_value if has_current_value else (
            prompt_default_overrides.get(ref.var_id, spec.default or "")
        )

        if spec.secret:
            if required and ref.var_id not in secret_required:
                secret_required.append(ref.var_id)
            continue

        should_prompt = ref.prompt and not has_current_value
        if required and not should_prompt and not has_current_value:
            should_prompt = True

        if should_prompt:
            value = _prompt_value(
                ref.var_id,
                spec.type,
                default_value,
                resolved.description,
                resolved.examples,
            )
        else:
            value = default_value

        values[ref.var_id] = value
        if ref.include_in_env_file and ref.var_id not in ordered_output_vars:
            ordered_output_vars.append(ref.var_id)


def _group_overlay_variable_refs(overlays: list[OverlaySpec]) -> list[tuple[OverlaySpec, list]]:
    winning_refs = {}
    winning_overlay_ids = {}
    for overlay in overlays:
        for ref in overlay.variables:
            current = winning_refs.get(ref.var_id)
            if current is None or ref.prompt_order < current.prompt_order:
                winning_refs[ref.var_id] = ref
                winning_overlay_ids[ref.var_id] = overlay.overlay_id

    grouped: list[tuple[OverlaySpec, list]] = []
    for overlay in overlays:
        refs = []
        seen_var_ids: set[str] = set()
        for ref in overlay.variables:
            if winning_overlay_ids.get(ref.var_id) != overlay.overlay_id:
                continue
            if ref.var_id in seen_var_ids:
                continue
            refs.append(winning_refs[ref.var_id])
            seen_var_ids.add(ref.var_id)
        if refs:
            grouped.append((overlay, refs))
    return grouped


def _overlay_prompt_title(overlay: OverlaySpec) -> str:
    if overlay.overlay_id == "base.runtime":
        return "Core runtime settings"
    return f"{overlay.title} settings"


def _overlay_prompt_summary(overlay: OverlaySpec) -> str:
    if overlay.overlay_id == "base.runtime":
        return "Shared settings used across the selected runtime mode, features, and apps."
    return overlay.summary


def _overlay_has_visible_prompts(
    schema: EnvSchema,
    refs,
    *,
    values: dict[str, str],
    context: SelectionContext,
) -> bool:
    for ref in refs:
        if not ref_is_applicable(ref, context=context, env=values):
            continue
        spec = schema.catalog[ref.var_id]
        if spec.secret:
            continue
        current_value = values.get(ref.var_id, "")
        has_current_value = _has_value(current_value)
        required = ref_is_required(ref, context=context, env=values)
        if ref.prompt and not has_current_value:
            return True
        if required and not has_current_value:
            return True
    return False


def _variable_refs_have_visible_prompts(
    schema: EnvSchema,
    refs,
    *,
    values: dict[str, str],
    context: SelectionContext,
) -> bool:
    for ref in refs:
        if not ref_is_applicable(ref, context=context, env=values):
            continue
        spec = schema.catalog[ref.var_id]
        if spec.secret:
            continue
        current_value = values.get(ref.var_id, "")
        has_current_value = _has_value(current_value)
        required = ref_is_required(ref, context=context, env=values)
        if ref.prompt and not has_current_value:
            return True
        if required and not has_current_value:
            return True
    return False


def _model_image_var_for_role(role: str) -> str:
    return {
        "llm": "VLLM_OPENAI_IMAGE_LLM",
        "embedding": "VLLM_OPENAI_IMAGE_EMBEDDING",
        "stt": "VLLM_OPENAI_IMAGE_STT",
    }.get(role, "")


def _model_max_model_len_var_for_role(role: str) -> str:
    return {
        "llm": "VLLM_LLM_MAX_MODEL_LEN",
        "embedding": "VLLM_EMBEDDING_MAX_MODEL_LEN",
        "stt": "VLLM_STT_MAX_MODEL_LEN",
    }.get(role, "")


def _model_variable_example_default(family: ModelFamilySpec, var_id: str) -> str:
    for ref in family.variables:
        if ref.var_id != var_id:
            continue
        if ref.examples:
            return ref.examples[0].strip()
        break
    return ""


def _model_variable_prompt_defaults(
    family: ModelFamilySpec,
    *,
    values: dict[str, str],
) -> dict[str, str]:
    defaults: dict[str, str] = {}

    image_var = _model_image_var_for_role(family.role)
    if image_var and not _has_value(values.get(image_var, "")):
        default_image = (values.get("VLLM_OPENAI_IMAGE", "") or "").strip()
        if not default_image:
            default_image = family.runtime.default_vllm_openai_image
        if default_image and _model_variable_example_default(family, image_var):
            defaults[image_var] = default_image

    max_model_len_var = _model_max_model_len_var_for_role(family.role)
    if max_model_len_var and not _has_value(values.get(max_model_len_var, "")):
        default_max_model_len = _model_variable_example_default(family, max_model_len_var)
        if default_max_model_len:
            defaults[max_model_len_var] = default_max_model_len

    return defaults


def run_wizard(
    root_dir: Path | str,
    *,
    prefill_env_path: Path | None = None,
    existing_env_mode: str = "ask",
) -> int:
    root_path = Path(root_dir).resolve()
    schema = load_env_schema(str(root_path), strict=True)
    mode_overlays = schema.by_kind("mode")
    if not mode_overlays:
        print("No mode overlays found in schema.")
        return 1

    print_ukbgpt_banner(
        "Environment Wizard",
        "Interactive setup for runtime mode, optional services, and model deployments.",
    )
    print("This wizard writes non-secret variables to .env.")
    print("Secret variables are never written to disk.")

    env_path = root_path / ".env"
    effective_prefill_path = prefill_env_path
    if effective_prefill_path is None and env_path.exists():
        selected_mode = existing_env_mode
        if selected_mode == "ask":
            _print_section(
                "Existing configuration",
                "A .env file already exists. Choose whether to extend it or overwrite it.",
            )
            print("1. Extend existing configuration")
            print("2. Overwrite from scratch")
            print("3. Cancel")
            selected_idx = _prompt_choice_required("Selection: ", ["prefill", "overwrite", "cancel"])
            selected_mode = ["prefill", "overwrite", "cancel"][selected_idx]

        if selected_mode == "cancel":
            print("Wizard cancelled.")
            return 1
        if selected_mode == "prefill":
            effective_prefill_path = env_path

    prefill_values: dict[str, str] = {}
    if effective_prefill_path is not None:
        try:
            prefill_values = load_env_file_values(effective_prefill_path)
        except ValueError as exc:
            print(str(exc))
            return 1

    if prefill_values:
        print(
            f"\n[Prefill] Loaded {len(prefill_values)} prefilled values from {effective_prefill_path}. "
            "Already-set values will be reused without prompting.\n"
        )

    values: dict[str, str] = dict(prefill_values)
    mode_by_name = {mode.name: mode for mode in mode_overlays}
    selected_mode_overlay = None
    prefilled_mode_raw = values.get("BATCH_CLIENT_MODE_ON", "")
    if _has_value(prefilled_mode_raw):
        prefilled_mode_name = _mode_from_selection(schema_parse_bool(prefilled_mode_raw, False))
        selected_mode_overlay = mode_by_name.get(prefilled_mode_name)

    _print_section(
        "1. Runtime mode",
        "Choose how the stack is exposed and which overlays are available.",
    )
    print("Select runtime mode:")
    for idx, mode in enumerate(mode_overlays, start=1):
        print(f"{idx}. {mode.title} - {mode.summary}")
    if selected_mode_overlay is None:
        mode_idx = _prompt_choice("Mode [default 1]: ", [m.name for m in mode_overlays], default_index=0)
        selected_mode_overlay = mode_overlays[mode_idx]
    else:
        print(f"Using prefilled mode: {selected_mode_overlay.title}")

    batch_mode = selected_mode_overlay.name == "batch_client"
    values["BATCH_CLIENT_MODE_ON"] = "true" if batch_mode else "false"
    print(f"Selected mode: {selected_mode_overlay.title}")

    enabled_features: set[str] = set()
    enabled_feature_titles: list[str] = []
    _print_section(
        "2. Feature settings",
        "Enable optional infrastructure and egress capabilities.",
    )
    for feature in schema.by_kind("feature"):
        if not feature.wizard_enabled:
            continue
        if feature.name in {"embedding_backend", "stt_backend"}:
            continue
        if feature.availability_modes and selected_mode_overlay.name not in feature.availability_modes:
            if feature.toggle_var:
                values[feature.toggle_var] = "false"
            continue

        has_prefilled_toggle = bool(feature.toggle_var) and _has_value(values.get(feature.toggle_var, ""))
        if has_prefilled_toggle and feature.toggle_var:
            enabled = schema_parse_bool(values.get(feature.toggle_var, ""), False)
        else:
            default_enabled = schema_parse_bool(
                schema.catalog.get(feature.toggle_var).default
                if feature.toggle_var and schema.catalog.get(feature.toggle_var)
                else "",
                False,
            )
            enabled = _prompt_yes_no(f"Enable feature: {feature.title}? {feature.summary}", default_enabled)
        if feature.toggle_var:
            values[feature.toggle_var] = "true" if enabled else "false"
        if enabled:
            enabled_features.add(feature.name)
            enabled_feature_titles.append(feature.title)
    print(f"Enabled features: {_format_summary(enabled_feature_titles)}")

    enabled_apps: set[str] = set()
    enabled_app_titles: list[str] = []
    _print_section(
        "3. App settings",
        "Enable optional applications that sit on top of the selected runtime mode.",
    )
    for app in schema.by_kind("app"):
        if not app.wizard_enabled:
            continue
        if app.availability_modes and selected_mode_overlay.name not in app.availability_modes:
            if app.toggle_var:
                values[app.toggle_var] = "false"
            continue

        has_prefilled_toggle = bool(app.toggle_var) and _has_value(values.get(app.toggle_var, ""))
        if has_prefilled_toggle and app.toggle_var:
            enabled = schema_parse_bool(values.get(app.toggle_var, ""), False)
        else:
            default_enabled = schema_parse_bool(
                schema.catalog.get(app.toggle_var).default
                if app.toggle_var and schema.catalog.get(app.toggle_var)
                else "",
                False,
            )
            enabled = _prompt_yes_no(f"Enable app: {app.title}? {app.summary}", default_enabled)
        if app.toggle_var:
            values[app.toggle_var] = "true" if enabled else "false"
        if enabled:
            enabled_apps.add(app.name)
            enabled_app_titles.append(app.title)
    print(f"Enabled apps: {_format_summary(enabled_app_titles)}")

    secret_required: list[str] = []
    descriptions: dict[str, str] = {}
    ordered_output_vars: list[str] = []
    selected_model_families: dict[str, ModelFamilySpec] = {}

    model_roles: list[tuple[str, str, bool]] = [
        ("llm", "MODEL_DEPLOYMENT_CONFIG", True),
        ("embedding", "EMBEDDING_MODEL_DEPLOYMENT_CONFIG", True),
        ("stt", "STT_MODEL_DEPLOYMENT_CONFIG", True),
        ("tts", "TTS_MODEL_DEPLOYMENT_CONFIG", True),
    ]

    _print_section(
        "4. Model deployments",
        "Select the model family and generate a deployment spec for each backend class you want to run.",
    )
    print("Offline model posture: required model artifacts must already be present locally before startup.")
    print("If you use Hugging Face models, pre-populate your HF cache manually, for example with: hf download <model_id>")
    for role, var_name, allow_disable in model_roles:
        try:
            selected_value, selected_family = _select_model_deployment_for_role(
                schema,
                values=values,
                role=role,
                var_name=var_name,
                allow_disable=allow_disable,
                root_dir=root_path,
            )
        except ValueError as exc:
            print(str(exc))
            return 1

        values[var_name] = selected_value
        if var_name in schema.catalog:
            descriptions[var_name] = schema.catalog[var_name].description
        if var_name not in ordered_output_vars:
            ordered_output_vars.append(var_name)
        if selected_family is not None:
            selected_model_families[selected_family.family_id] = selected_family
            print(f"Selected {_model_role_label(role)} deployment family: {selected_family.title}")

            current_enabled_features = set(enabled_features)
            if role == "embedding" and _has_value(selected_value):
                current_enabled_features.add("embedding_backend")
            elif role == "stt" and _has_value(selected_value):
                current_enabled_features.add("stt_backend")

            current_context = SelectionContext(
                mode=_mode_from_selection(batch_mode),
                enabled_features=frozenset(current_enabled_features),
                enabled_apps=frozenset(enabled_apps),
            )
            family_refs = _merge_variable_refs(list(selected_family.variables))
            prompt_default_overrides = _model_variable_prompt_defaults(
                selected_family,
                values=values,
            )
            if _variable_refs_have_visible_prompts(
                schema,
                family_refs,
                values=values,
                context=current_context,
            ):
                _print_subsection(
                    f"{_model_role_label(role)} model values",
                    f"Additional values for {selected_family.title}.",
                )
            _apply_variable_refs(
                schema,
                family_refs,
                values=values,
                context=current_context,
                descriptions=descriptions,
                ordered_output_vars=ordered_output_vars,
                secret_required=secret_required,
                prompt_default_overrides=prompt_default_overrides,
            )
        elif selected_value:
            print(f"Selected {_model_role_label(role)} deployment config: {selected_value}")
        else:
            print(f"{_model_role_label(role)} backend: disabled")

    if _has_value(values.get("EMBEDDING_MODEL_DEPLOYMENT_CONFIG", "")):
        enabled_features.add("embedding_backend")
    if _has_value(values.get("STT_MODEL_DEPLOYMENT_CONFIG", "")):
        enabled_features.add("stt_backend")
    if _has_value(values.get("TTS_MODEL_DEPLOYMENT_CONFIG", "")):
        enabled_features.add("tts_backend")

    configured_model_vars = [var_name for _role, var_name, _allow_disable in model_roles]
    if not any(_has_value(values.get(var_name, "")) for var_name in configured_model_vars):
        print("At least one backend model deployment must be selected (LLM, embedding, STT, or TTS).")
        return 1

    context = SelectionContext(
        mode=_mode_from_selection(batch_mode),
        enabled_features=frozenset(enabled_features),
        enabled_apps=frozenset(enabled_apps),
    )

    overlays = schema_selected_overlays(schema, context=context, env=values)
    _print_section(
        "5. Environment values",
        "Provide non-secret values required by the selected mode, features, and apps.",
    )
    for overlay, overlay_refs in _group_overlay_variable_refs(overlays):
        if not _overlay_has_visible_prompts(schema, overlay_refs, values=values, context=context):
            _apply_variable_refs(
                schema,
                overlay_refs,
                values=values,
                context=context,
                descriptions=descriptions,
                ordered_output_vars=ordered_output_vars,
                secret_required=secret_required,
            )
            continue

        _print_subsection(
            _overlay_prompt_title(overlay),
            _overlay_prompt_summary(overlay),
        )
        _apply_variable_refs(
            schema,
            overlay_refs,
            values=values,
            context=context,
            descriptions=descriptions,
            ordered_output_vars=ordered_output_vars,
            secret_required=secret_required,
        )

    model_refs = _merge_variable_refs(
        [ref for family in selected_model_families.values() for ref in family.variables]
    )
    if _variable_refs_have_visible_prompts(
        schema,
        model_refs,
        values=values,
        context=context,
    ):
        _print_section(
            "6. Model-specific values",
            "Provide any additional non-secret values required by the selected model families.",
        )
    _apply_variable_refs(
        schema,
        model_refs,
        values=values,
        context=context,
        descriptions=descriptions,
        ordered_output_vars=ordered_output_vars,
        secret_required=secret_required,
    )

    _print_section(
        "7. Output",
        "Non-secret values are written to disk. Required secrets stay in shell-only exports.",
    )
    _write_env_file(
        env_path,
        ordered_var_names=ordered_output_vars,
        values=values,
        descriptions=descriptions,
    )

    print(f"Wrote non-secret configuration to: {env_path}")

    if secret_required:
        print("\nRequired secrets (not written to .env):")
        for var_name in sorted(secret_required):
            print(f"  - {var_name}")

        print("\nSafer export style (avoids typing secret in shell history):")
        for var_name in sorted(secret_required):
            print(f"  read -sr {var_name}; echo; export {var_name}")

        print("\nPlaceholder export style:")
        for var_name in sorted(secret_required):
            print(f"  export {var_name}=\"<set-{var_name.lower()}>\"")

        print("\nFile-based import style:")
        for var_name in sorted(secret_required):
            file_var = f"{var_name}_FILE"
            if var_name == "CERTIFICATE_KEY":
                example_path = "/path/to/server.key"
            elif var_name == "WEBUI_SECRET_KEY":
                example_path = "/path/to/webui_secret_key.txt"
            else:
                example_path = f"/path/to/{var_name.lower()}.txt"
            print(f"  export {file_var}=\"{example_path}\"")

        print("\nNotes:")
        print("  - start.py accepts VAR or VAR_FILE, but not both at the same time; if both are set, startup aborts.")
        print("  - VAR_FILE is useful when the secret already exists as a local file.")
        print("  - If you created a temporary secret file only for startup, you can delete it after startup begins.")

    print("\nNext step:")
    print("  python3 start.py up")
    return 0


def _bool_default_from_schema(var_name: str, default: bool = False) -> bool:
    raw_default = _schema_default(var_name, "")
    if raw_default == "":
        return default
    return schema_parse_bool(raw_default, default)


def _set_env_default_from_schema(var_name: str) -> None:
    spec_default = _schema_default(var_name, "")
    if spec_default == "":
        return
    current = os.getenv(var_name)
    if current is None or not current.strip():
        os.environ[var_name] = spec_default


def _is_model_config_requested(raw_value: str | None) -> bool:
    value = (raw_value or "").strip().lower()
    return value not in {"", "none", "off", "disable", "disabled"}


def _resolve_api_egress_toggle_with_compat(_batch_mode: bool) -> bool:
    """
    Compatibility behavior:
    - explicit ENABLE_API_EGRESS=true/false has priority
    - when unset, infer from additional address vars and warn if inferred
    """
    raw_toggle = os.getenv("ENABLE_API_EGRESS")
    additional_api = env_str("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS")
    additional_embedding_api = env_str("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS")

    explicit_toggle = raw_toggle is not None
    os.environ["UKBGPT_API_EGRESS_TOGGLE_EXPLICIT"] = "true" if explicit_toggle else "false"
    if explicit_toggle:
        os.environ["UKBGPT_API_EGRESS_EXPLICIT_VALUE"] = raw_toggle.strip().lower()
    else:
        os.environ["UKBGPT_API_EGRESS_EXPLICIT_VALUE"] = ""

    if raw_toggle is None:
        inferred = bool(additional_api or additional_embedding_api)
        if inferred:
            print(
                "⚠️  [Compat] ENABLE_API_EGRESS is unset. "
                "Inferring enabled state from additional API address variables. "
                "Set ENABLE_API_EGRESS explicitly to silence this warning."
            )
        os.environ["UKBGPT_API_EGRESS_INFERRED_COMPAT"] = "true" if inferred else "false"
        enabled = inferred
    else:
        os.environ["UKBGPT_API_EGRESS_INFERRED_COMPAT"] = "false"
        enabled = raw_toggle.strip().lower() in TRUE_VALUES

    os.environ["ENABLE_API_EGRESS"] = "true" if enabled else "false"
    return enabled


def _resolve_backend_toggle_with_compat(
    *,
    feature_title: str,
    toggle_var: str,
    config_var: str,
) -> bool:
    """
    Compatibility behavior:
    - deployment config presence is the source of truth
    - legacy toggle vars are ignored when a deployment config is present
    - legacy toggle vars without a deployment config fail fast with a migration hint
    """
    raw_toggle = os.getenv(toggle_var)
    config_requested = _is_model_config_requested(os.getenv(config_var))

    if raw_toggle is None:
        os.environ[toggle_var] = "true" if config_requested else "false"
        return config_requested

    toggle_enabled = raw_toggle.strip().lower() in TRUE_VALUES
    if config_requested:
        if not toggle_enabled:
            print(
                f"⚠️  [Compat] {toggle_var} is set but deprecated. "
                f"{config_var} now controls {feature_title} enablement. "
                f"Ignoring {toggle_var}."
            )
        os.environ[toggle_var] = "true"
        return True

    if toggle_enabled:
        print(f"❌ Error: {toggle_var}=true without {config_var} is no longer supported.")
        print(f"   Action: unset {toggle_var} and set {config_var} instead.")
        sys.exit(1)

    os.environ[toggle_var] = "false"
    return False


def _resolve_chat_purger_toggle_with_compat(batch_mode: bool) -> bool:
    raw_toggle = os.getenv("ENABLE_CHAT_PURGER")
    if raw_toggle is None:
        inferred = bool(env_str("CHAT_HISTORY_RETENTION_DAYS"))
        if inferred:
            print(
                "⚠️  [Compat] ENABLE_CHAT_PURGER is unset. "
                "Inferring enabled state from CHAT_HISTORY_RETENTION_DAYS. "
                "Set ENABLE_CHAT_PURGER explicitly to silence this warning."
            )
        enabled = inferred
    else:
        enabled = raw_toggle.strip().lower() in TRUE_VALUES

    if batch_mode and enabled:
        print("⚠️  [Feature] Chat Purger requested, but Batch Client Mode is active. Disabling.")
        enabled = False

    os.environ["ENABLE_CHAT_PURGER"] = "true" if enabled else "false"
    return enabled


def _resolve_schema_runtime_selection() -> SchemaRuntimeSelection:
    schema = _load_runtime_env_schema()

    batch_mode_default = _bool_default_from_schema("BATCH_CLIENT_MODE_ON", False)
    batch_mode = env_bool("BATCH_CLIENT_MODE_ON", batch_mode_default)
    mode_name = "batch_client" if batch_mode else "chatbot_provider"
    os.environ["BATCH_CLIENT_MODE_ON"] = "true" if batch_mode else "false"

    enabled_features: dict[str, bool] = {}
    for feature_overlay in schema.by_kind("feature"):
        if feature_overlay.name == "embedding_backend":
            enabled = _resolve_backend_toggle_with_compat(
                feature_title=feature_overlay.title,
                toggle_var="ENABLE_EMBEDDING_BACKEND",
                config_var="EMBEDDING_MODEL_DEPLOYMENT_CONFIG",
            )
            os.environ["ENABLE_EMBEDDING_BACKEND"] = "true" if enabled else "false"
        elif feature_overlay.name == "stt_backend":
            enabled = _resolve_backend_toggle_with_compat(
                feature_title=feature_overlay.title,
                toggle_var="ENABLE_STT_BACKEND",
                config_var="STT_MODEL_DEPLOYMENT_CONFIG",
            )
            os.environ["ENABLE_STT_BACKEND"] = "true" if enabled else "false"
        elif feature_overlay.name == "tts_backend":
            enabled = _resolve_backend_toggle_with_compat(
                feature_title=feature_overlay.title,
                toggle_var="ENABLE_TTS_BACKEND",
                config_var="TTS_MODEL_DEPLOYMENT_CONFIG",
            )
            os.environ["ENABLE_TTS_BACKEND"] = "true" if enabled else "false"
        elif not feature_overlay.toggle_var:
            enabled_features[feature_overlay.name] = False
            continue
        else:
            toggle_var = feature_overlay.toggle_var
            if feature_overlay.name == "api_egress":
                enabled = _resolve_api_egress_toggle_with_compat(batch_mode)
            elif feature_overlay.name == "chat_purger":
                enabled = _resolve_chat_purger_toggle_with_compat(batch_mode)
            else:
                toggle_default = _bool_default_from_schema(toggle_var, False)
                enabled = env_bool(toggle_var, toggle_default)

            if enabled and feature_overlay.availability_modes and mode_name not in feature_overlay.availability_modes:
                print(
                    f"⚠️  [Feature] {feature_overlay.title} requested, but {mode_name} does not support it. Disabling."
                )
                enabled = False

            if toggle_var:
                os.environ[toggle_var] = "true" if enabled else "false"
            enabled_features[feature_overlay.name] = enabled
            continue

        if enabled and feature_overlay.availability_modes and mode_name not in feature_overlay.availability_modes:
            print(
                f"⚠️  [Feature] {feature_overlay.title} requested, but {mode_name} does not support it. Disabling."
            )
            enabled = False

        enabled_features[feature_overlay.name] = enabled

    enabled_apps: dict[str, bool] = {}
    for app_overlay in schema.by_kind("app"):
        toggle_var = app_overlay.toggle_var
        if not toggle_var:
            enabled_apps[app_overlay.name] = False
            continue

        toggle_default = _bool_default_from_schema(toggle_var, False)
        enabled = env_bool(toggle_var, toggle_default)
        if enabled and app_overlay.availability_modes and mode_name not in app_overlay.availability_modes:
            print(
                f"⚠️  [App] {app_overlay.title} requested, but {mode_name} does not support it. Disabling."
            )
            enabled = False

        os.environ[toggle_var] = "true" if enabled else "false"
        enabled_apps[app_overlay.name] = enabled

    context = SelectionContext(
        mode=mode_name,
        enabled_features=frozenset(name for name, enabled in enabled_features.items() if enabled),
        enabled_apps=frozenset(name for name, enabled in enabled_apps.items() if enabled),
    )

    overlays = schema_selected_overlays(schema, context=context, env=os.environ)

    # Phase-2: defaults and applicability are resolved via TOML metadata.
    for overlay in overlays:
        for ref in overlay.variables:
            if not ref_is_applicable(ref, context=context, env=os.environ):
                continue
            spec = schema.catalog[ref.var_id]
            if spec.default is None:
                continue
            if spec.default == "" and spec.type != "bool":
                continue
            _set_env_default_from_schema(ref.var_id)

    return SchemaRuntimeSelection(
        schema=schema,
        context=context,
        overlays=overlays,
        enabled_features=enabled_features,
        enabled_apps=enabled_apps,
    )


def _schema_required_missing(selection: SchemaRuntimeSelection) -> list[str]:
    missing: list[str] = []
    for overlay in selection.overlays:
        for ref in overlay.variables:
            if not ref_is_applicable(ref, context=selection.context, env=os.environ):
                continue
            if not ref_is_required(ref, context=selection.context, env=os.environ):
                continue
            if env_str(ref.var_id):
                continue
            if ref.var_id not in missing:
                missing.append(ref.var_id)
    return missing


def _has_pem_block(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _validate_key_material(value: str, *, label: str) -> bool:
    """Return True when KEY material looks like PEM text."""
    if not value:
        return False

    if _has_pem_block(value, _EXPECTED_KEY_MARKERS):
        return True

    if _has_pem_block(value, _EXPECTED_CERT_MARKERS):
        print(f"❌ Error: {label} looks like a certificate (CERTIFICATE block found), not a private key.")
        print(f"   Action: export the matching private key content to {label}.")
        print("         Example: CERTIFICATE_KEY=\"$(cat /path/to/server.key)\"")
        return False

    print(f"❌ Error: {label} does not look like PEM key material.")
    print(f"   Action: verify {label} is a PEM private key (BEGIN/END PRIVATE KEY block).")
    return False


def _validate_pem_certificate_file(path: str, *, label: str) -> bool:
    """Return True for files that look like PEM certificate files."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            contents = handle.read(64 * 1024)
    except OSError as exc:
        print(f"❌ Error: Unable to read {label}: {path}")
        print(f"   Reason: {exc}")
        return False

    if not contents.strip():
        print(f"❌ Error: {label} is empty: {path}")
        return False

    if _has_pem_block(contents, _EXPECTED_KEY_MARKERS):
        print(f"❌ Error: {label} contains private-key blocks; it should contain certificate(s).")
        print(f"   Action: point {label} at a PEM certificate chain (server + intermediates).")
        return False

    if not _has_pem_block(contents, _EXPECTED_CERT_MARKERS):
        print(f"❌ Error: {label} does not contain a PEM certificate block: {path}")
        print("   Nginx expected /etc/nginx/certs/fullchain.pem to be PEM encoded certificates.")
        print("   Action: export a certificate chain file containing -----BEGIN CERTIFICATE----- blocks.")
        return False

    first_line = _first_non_empty_non_comment_line(contents)
    if first_line and not first_line.startswith("-----BEGIN "):
        print(f"❌ Error: {label} appears to contain non-PEM preamble before the first PEM block: {path}")
        print("   Nginx expects the first non-empty line to start with '-----BEGIN CERTIFICATE-----'.")
        print("   Action: remove any comments/headers like 'Bag Attributes' and re-export as plain PEM.")
        return False

    openssl_ok = _openssl_certificate_inspect(path)
    if openssl_ok is False:
        print(f"❌ Error: {label} is not a valid PEM certificate as read by openssl: {path}")
        print("   Nginx expects /etc/nginx/certs/fullchain.pem to be a PEM certificate file.")
        print("   Action: re-export the certificate with PEM format (e.g. openssl x509 -in ... -out server.pem).")
        return False

    cert_block_count = _count_pem_cert_blocks(contents)
    if cert_block_count == 1 and "fullchain" not in os.path.basename(path).lower():
        print("⚠️  Warning: SSL_CERT_PATH currently contains a single CERTIFICATE block.")
        print("   Nginx mounts this as /etc/nginx/certs/fullchain.pem and expects a fullchain chain")
        print("   (server certificate + intermediate CA certificate(s)).")
        print("   Action: provide a PEM chain file with intermediates, commonly named fullchain.pem.")
    elif cert_block_count >= _FULLCHAIN_CERT_COUNT_HINT:
        print(f"🔒 Info: SSL certificate chain detected in {label} ({cert_block_count} certificate blocks).")

    return True


def _validate_rfc1918_private_ipv4(var_name: str, value: str) -> str | None:
    raw_value = (value or "").strip()
    if not raw_value:
        print(f"❌ Error: {var_name} is not set.")
        return None

    try:
        parsed_ip = ipaddress.ip_address(raw_value)
    except ValueError:
        print(f"❌ Error: {var_name} must be a valid IP address.")
        return None

    if not isinstance(parsed_ip, ipaddress.IPv4Address) or not any(
        parsed_ip in net for net in RFC1918_PRIVATE_V4_NETWORKS
    ):
        print(
            f"❌ Error: {var_name} must be an RFC1918 private IPv4 address "
            "(10/8, 172.16/12, 192.168/16)."
        )
        return None

    return raw_value


def _encode_firewall_egress_rules(rules: list[tuple[str, str, int]]) -> str:
    encoded: list[str] = []
    seen: set[tuple[str, str, int]] = set()
    for protocol, address, port in rules:
        key = (protocol.lower(), address, int(port))
        if key in seen:
            continue
        seen.add(key)
        encoded.append(f"{key[0]}|{key[1]}|{key[2]}")
    return ",".join(encoded)


def _host_total_memory_bytes() -> int:
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        phys_pages = int(os.sysconf("SC_PHYS_PAGES"))
        total = page_size * phys_pages
        if total > 0:
            return total
    except (AttributeError, OSError, ValueError):
        pass

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    fields = line.split()
                    if len(fields) >= 2 and fields[1].isdigit():
                        return int(fields[1]) * 1024
    except OSError:
        pass

    return 0


def _host_available_memory_bytes() -> int:
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        avail_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        total = page_size * avail_pages
        if total > 0:
            return total
    except (AttributeError, OSError, ValueError):
        pass

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    fields = line.split()
                    if len(fields) >= 2 and fields[1].isdigit():
                        return int(fields[1]) * 1024
    except OSError:
        pass

    return 0


def _parse_cpu_id_set(raw: str) -> list[int]:
    """
    Parse Linux CPU list notation like "0-3,6,8-9" into a sorted unique list.
    Invalid chunks are ignored.
    """
    out: set[int] = set()
    for chunk in (raw or "").split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            if not left.strip().isdigit() or not right.strip().isdigit():
                continue
            start = int(left.strip())
            end = int(right.strip())
            if end < start:
                start, end = end, start
            out.update(range(start, end + 1))
            continue
        if part.isdigit():
            out.add(int(part))
    return sorted(out)


def _compress_cpu_ids(ids: list[int]) -> str:
    if not ids:
        return "0"

    ranges = []
    start = prev = ids[0]
    for cpu in ids[1:]:
        if cpu == prev + 1:
            prev = cpu
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = cpu
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)


def _available_cpu_ids() -> list[int]:
    cpuset_paths = (
        "/sys/fs/cgroup/cpuset.cpus.effective",  # cgroup v2
        "/sys/fs/cgroup/cpuset/cpuset.cpus",     # cgroup v1
    )
    cpu_count = max(1, int(os.cpu_count() or 1))

    for path in cpuset_paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                ids = _parse_cpu_id_set(handle.read().strip())
                if ids:
                    bounded = [cpu for cpu in ids if 0 <= cpu < cpu_count]
                    if bounded:
                        return bounded
                    return ids
        except OSError:
            pass

    return list(range(cpu_count))


def _default_dataset_structuring_cpuset() -> str:
    cpu_ids = _available_cpu_ids()
    if not cpu_ids:
        return "0"
    if len(cpu_ids) == 1:
        return str(cpu_ids[0])

    # Keep CPU0 for host responsiveness when available.
    filtered = [cpu for cpu in cpu_ids if cpu != 0]
    if not filtered:
        filtered = cpu_ids
    return _compress_cpu_ids(filtered)


def _default_dataset_structuring_mem_limit() -> str:
    available_bytes = _host_available_memory_bytes()
    total_bytes = _host_total_memory_bytes()
    if available_bytes > 0 and total_bytes > 0:
        source_bytes = min(available_bytes, total_bytes)
    elif available_bytes > 0:
        source_bytes = available_bytes
    else:
        source_bytes = total_bytes
    if source_bytes <= 0:
        return "4g"

    limit_mib = max(
        _DATASET_STRUCTURING_MIN_MEMORY_LIMIT_MIB,
        int((source_bytes * _DATASET_STRUCTURING_DEFAULT_MEMORY_RATIO) // _MEBIBYTE),
    )
    return f"{limit_mib}m"


def _apply_dataset_structuring_resource_defaults() -> None:
    if not env_str("DATASET_STRUCTURING_CPUSET"):
        cpuset = _default_dataset_structuring_cpuset()
        os.environ["DATASET_STRUCTURING_CPUSET"] = cpuset
        print(f"🔒 Info: Dataset structuring CPU affinity default applied: {cpuset}")

    if not env_str("DATASET_STRUCTURING_MEM_LIMIT"):
        mem_limit = _default_dataset_structuring_mem_limit()
        os.environ["DATASET_STRUCTURING_MEM_LIMIT"] = mem_limit
        print(
            "🔒 Info: Dataset structuring memory limit default applied: "
            f"{mem_limit} (~90% of available RAM)"
        )

    print(
        "🔒 Info: Dataset structuring effective resource settings: "
        f"cpuset={os.environ.get('DATASET_STRUCTURING_CPUSET', '').strip() or 'unset'}, "
        f"mem_limit={os.environ.get('DATASET_STRUCTURING_MEM_LIMIT', '').strip() or 'unset'}"
    )


def _resolve_repo_or_absolute_path(path_value: str) -> str:
    expanded = os.path.expanduser(path_value.strip())
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    return os.path.abspath(os.path.join(ROOT_DIR, expanded))


def _ensure_llm_structured_outputs_backend_xgrammar() -> None:
    flag = "--structured-outputs-config.backend=xgrammar"
    current = env_str("VLLM_LLM_COMMAND_APPEND")
    tokens = shlex.split(current) if current else []
    if flag in tokens:
        return
    os.environ["VLLM_LLM_COMMAND_APPEND"] = f"{current} {flag}".strip()
    print("🔒 Info: Enabled xgrammar structured outputs for the LLM worker profile.")


def setup_logging(log_dir=None, *, announce=True, show_session_header=True):
    """
    Sets up logging to both stdout and a single startup log file.
    Matches the global logging setup in start.py.
    """
    if log_dir is None:
        log_dir = ROOT_DIR

    log_file = os.path.join(log_dir, "start.log")

    class Tee:
        def __init__(self, filename, stream):
            self._is_startup_tee = True
            self._wrapped_stream = stream
            self.file = open(filename, "w", encoding="utf-8")

        def write(self, data):
            self.file.write(data)
            self._wrapped_stream.write(data)
            self.file.flush()

        def flush(self):
            self.file.flush()
            self._wrapped_stream.flush()

        def close(self):
            self.file.close()

    wrapped_stream = sys.stdout
    if getattr(wrapped_stream, "_is_startup_tee", False):
        wrapped_stream.close()
        wrapped_stream = wrapped_stream._wrapped_stream

    tee = Tee(log_file, wrapped_stream)
    sys.stdout = tee
    sys.stderr = tee

    if announce:
        print(f"📝 Logging entire startup session to: {log_file}")
    if show_session_header:
        print("### UKB-GPT Standalone Startup ###")
        print(f"Date: {datetime.datetime.now()}")

    return log_file

def _format_cmd(cmd):
    return shlex.join([str(part) for part in cmd])

def run_command(cmd, check=True, capture_output=False, silent=False):
    """
    Helper to run commands and exit on failure.
    If silent is True, it suppresses normal output but prints on failure.
    """
    if not isinstance(cmd, (list, tuple)) or not cmd:
        print(f"\n❌ Error: run_command expects a non-empty command list, got: {cmd!r}")
        sys.exit(1)

    cmd = [str(part) for part in cmd]
    cmd_text = _format_cmd(cmd)
    should_capture = capture_output and not silent

    try:
        if not silent:
            print(f"  [EXEC] {cmd_text}")

        if silent:
            with tempfile.TemporaryFile(mode="w+") as stdout_file, tempfile.TemporaryFile(
                mode="w+"
            ) as stderr_file:
                result = subprocess.run(
                    cmd,
                    check=False,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                )
                stdout_file.seek(0)
                stderr_file.seek(0)
                result.stdout = stdout_file.read()
                result.stderr = stderr_file.read()
        else:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=should_capture,
                text=True,
            )

        if check and result.returncode != 0:
            print(f"\n❌ Error: Command failed: {cmd_text}")
            if result.stdout:
                print("--- Stdout ---")
                print(result.stdout)
            if result.stderr:
                print("--- Stderr ---")
                print(result.stderr)
            sys.exit(1)

        return result
    except OSError as e:
        print(f"\n❌ Error: Failed to execute command: {cmd_text}")
        print(str(e))
        sys.exit(1)


def _print_subprocess_output(result: subprocess.CompletedProcess) -> None:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")


def _print_port_listener_diagnostics(batch_mode: bool) -> None:
    ports = set(range(5000, 5008))
    ports.add(8001)
    if batch_mode:
        raw = os.getenv("BATCH_CLIENT_LISTEN_PORT", "30000").strip() or "30000"
        try:
            ports.add(int(raw))
        except ValueError:
            pass
    else:
        ports.update({80, 443})

    try:
        probe = subprocess.run(["ss", "-H", "-ltnp"], capture_output=True, text=True, check=False)
    except OSError:
        return

    matches = []
    for line in probe.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        port_text = local.rsplit(":", 1)[-1]
        if not port_text.isdigit():
            continue
        if int(port_text) in ports:
            matches.append(line)

    if not matches:
        return

    print("🔎 Listener diagnostics for ingress-related ports:")
    for line in matches:
        print(f"   {line}")


def start_services(compose_args, launch_plan: StartupLaunchPlan, batch_mode: bool) -> None:
    start_launch_services(
        compose_args,
        launch_plan,
        batch_mode,
        format_cmd=_format_cmd,
        print_subprocess_output=_print_subprocess_output,
        port_listener_diagnostics_callback=_print_port_listener_diagnostics,
    )

def validate_environment(selection: SchemaRuntimeSelection | None = None) -> SchemaRuntimeSelection:
    """
    Validates that required environment variables are set.
    Matches the preflight configuration validation in start.py.
    """
    missing_config = False
    if selection is None:
        selection = _resolve_schema_runtime_selection()
    batch_mode = selection.context.mode == "batch_client"
    ldap_enabled = "ldap" in selection.context.enabled_features
    api_egress_enabled = "api_egress" in selection.context.enabled_features
    dataset_structuring_enabled = "dataset_structuring" in selection.context.enabled_apps
    cohort_feasibility_enabled = "cohort_feasibility" in selection.context.enabled_apps
    icd10_enabled = "icd_10_coding" in selection.context.enabled_apps

    additional_api = env_str("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS")
    additional_embedding_api = env_str("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS")
    os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ENABLED"] = "false"
    os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ENABLED"] = "false"
    os.environ["UKBGPT_EXPECT_EGRESS_BRIDGE"] = "true" if (ldap_enabled or api_egress_enabled) else "false"
    os.environ.pop("UKBGPT_FIREWALL_EGRESS_RULES", None)
    resolved_certificate_key = _resolve_secret_input("CERTIFICATE_KEY")
    resolved_webui_secret_key = _resolve_secret_input("WEBUI_SECRET_KEY")

    def _flag_missing(message: str, action: str = "") -> None:
        nonlocal missing_config
        print(message)
        if action:
            print(action)
        missing_config = True

    # TOML canonical required checks.
    schema_required_missing = _schema_required_missing(selection)
    # These variables still have specialized executable validators below; avoid duplicate generic errors.
    manual_required_vars = {
        "OPENWEBUI_DATA_DIR",
        "CERTIFICATE_KEY",
        "SSL_CERT_PATH",
        "WEBUI_SECRET_KEY",
        "ROOT_CA_PATH",
        "LDAP_TARGET_IP",
        "LDAP_TARGET_SNI",
        "DATASET_STRUCTURING_DATA_ROOT",
        "API_KEY",
        "EMBEDDING_API_KEY",
    }
    for var_name in schema_required_missing:
        if var_name in manual_required_vars:
            continue
        _flag_missing(
            f"❌ Error: {var_name} is required by active mode/feature/app selection from TOML schema."
        )

    hf_home = env_str("HF_HOME")
    if hf_home:
        if not os.path.isabs(hf_home) and not hf_home.startswith("~"):
            print("⚠️  Warning: HF_HOME is relative; compose bind mounts are resolved from compose project path.")
            print(f"   Action: use an absolute path to avoid mounting the wrong directory (e.g. {os.path.abspath(hf_home)}).")
        resolved_hf_home = os.path.abspath(os.path.expanduser(hf_home))
        if os.path.isfile(resolved_hf_home):
            _flag_missing(f"❌ Error: HF_HOME points to a file, not a directory: {resolved_hf_home}")
        else:
            os.environ["HF_HOME"] = resolved_hf_home

    # Frontend/chat retention persistence requirements (chatbot provider mode).
    if not batch_mode:
        openwebui_data_dir = env_str("OPENWEBUI_DATA_DIR")
        resolved_openwebui_data_dir = ""
        if not openwebui_data_dir:
            print(
                "⚠️ Warning: OPENWEBUI_DATA_DIR is unset. "
                "OpenWebUI local state path must be explicitly configured by the deployer."
            )
            _flag_missing(
                "❌ Error: OPENWEBUI_DATA_DIR is required in chatbot provider mode.",
                "   Action: export OPENWEBUI_DATA_DIR=\"/var/lib/ukbgpt/openwebui-data\"",
            )
        else:
            resolved_openwebui_data_dir = os.path.abspath(os.path.expanduser(openwebui_data_dir))
            if _path_is_within_directory(resolved_openwebui_data_dir, ROOT_DIR):
                _flag_missing(
                    "❌ Error: OPENWEBUI_DATA_DIR must not point inside the repository tree: "
                    f"{resolved_openwebui_data_dir}",
                    "   Action: use a dedicated host path outside the repo "
                    '(e.g. export OPENWEBUI_DATA_DIR="/var/lib/ukbgpt/openwebui-data").',
                )
                resolved_openwebui_data_dir = ""
            else:
                os.environ["OPENWEBUI_DATA_DIR"] = resolved_openwebui_data_dir

        runtime_uid = env_str("OPENWEBUI_RUNTIME_UID")
        runtime_gid = env_str("OPENWEBUI_RUNTIME_GID")
        if not runtime_uid:
            runtime_uid = str(os.getuid()) if os.getuid() != 0 else "999"
            os.environ["OPENWEBUI_RUNTIME_UID"] = runtime_uid
        if not runtime_gid:
            runtime_gid = str(os.getgid()) if os.getuid() != 0 else "999"
            os.environ["OPENWEBUI_RUNTIME_GID"] = runtime_gid

        runtime_uid_int = -1
        runtime_gid_int = -1
        for var_name in ("OPENWEBUI_RUNTIME_UID", "OPENWEBUI_RUNTIME_GID"):
            raw = env_str(var_name)
            if not raw.isdigit() or int(raw) <= 0:
                print(f"❌ Error: {var_name} must be a positive numeric id.")
                missing_config = True
        if not missing_config:
            runtime_uid_int = int(env_str("OPENWEBUI_RUNTIME_UID"))
            runtime_gid_int = int(env_str("OPENWEBUI_RUNTIME_GID"))

        if resolved_openwebui_data_dir:
            try:
                os.makedirs(resolved_openwebui_data_dir, exist_ok=True)
            except OSError as exc:
                print(
                    "❌ Error: Could not create OPENWEBUI_DATA_DIR "
                    f"at {resolved_openwebui_data_dir}: {exc}"
                )
                missing_config = True
            else:
                if not missing_config and os.getuid() == 0 and (runtime_uid_int, runtime_gid_int) != (0, 0):
                    try:
                        _chown_tree(resolved_openwebui_data_dir, runtime_uid_int, runtime_gid_int)
                        print(
                            "🔒 Info: OPENWEBUI_DATA_DIR ownership aligned for frontend runtime user: "
                            f"{runtime_uid_int}:{runtime_gid_int}"
                        )
                    except OSError as exc:
                        print(
                            "❌ Error: Could not adjust OPENWEBUI_DATA_DIR ownership for frontend runtime user "
                            f"{runtime_uid_int}:{runtime_gid_int}: {exc}"
                        )
                        missing_config = True

                if not missing_config and not _has_dir_write_access_for_identity(
                    resolved_openwebui_data_dir, runtime_uid_int, runtime_gid_int
                ):
                    print(
                        "❌ Error: OPENWEBUI_DATA_DIR is not writable/executable by frontend runtime user "
                        f"{runtime_uid_int}:{runtime_gid_int}: {resolved_openwebui_data_dir}"
                    )
                    missing_config = True
    
    # A. SSL Private Key (Standard Mode ONLY)
    if not batch_mode:
        certificate_key = resolved_certificate_key
        if not certificate_key:
            _flag_missing(
                "❌ Error: CERTIFICATE_KEY is not set.",
                "   Action: export CERTIFICATE_KEY=\"$(< /path/to/server.key)\"",
            )
        elif not _validate_key_material(certificate_key, label="CERTIFICATE_KEY"):
            missing_config = True
        
    # B. SSL Public Certificate (Standard Mode ONLY)
    if not batch_mode:
        ssl_cert_path = env_str("SSL_CERT_PATH")
        if not ssl_cert_path:
            _flag_missing(
                "❌ Error: SSL_CERT_PATH is not set.",
                "   Action: export SSL_CERT_PATH=\"/path/to/your/fullchain.pem\"",
            )
        else:
            if not os.path.isabs(ssl_cert_path):
                print("⚠️  Warning: SSL_CERT_PATH is relative; compose bind mounts are resolved from compose project path.")
                print(f"   Action: use absolute path to avoid mounting the wrong file (e.g. {os.path.abspath(ssl_cert_path)}).")
            ssl_cert_path = os.path.abspath(os.path.expanduser(ssl_cert_path))
            if os.path.isdir(ssl_cert_path):
                _flag_missing(f"❌ Error: SSL_CERT_PATH points to a directory, not a PEM file: {ssl_cert_path}")
                print("   Hint: docker-compose may create a host directory when bind source does not exist, then mount it as a directory target.")
            elif not os.path.isfile(ssl_cert_path):
                _flag_missing(f"❌ Error: SSL_CERT_PATH is not a file: {ssl_cert_path}")
            elif not _validate_pem_certificate_file(ssl_cert_path, label="SSL_CERT_PATH"):
                missing_config = True
            else:
                os.environ["SSL_CERT_PATH"] = ssl_cert_path

    # C. WebUI Secret (Standard Mode ONLY)
    if not batch_mode:
        if not resolved_webui_secret_key:
            _flag_missing(
                "❌ Error: WEBUI_SECRET_KEY is not set.",
                "   Action: export WEBUI_SECRET_KEY=\"<random-string>\"",
            )
        
    # D. Optional Root CA
    root_ca_path = env_str("ROOT_CA_PATH")
    if root_ca_path:
        if not os.path.isfile(root_ca_path):
            print(f"❌ Error: ROOT_CA_PATH defined but file not found: {root_ca_path}")
            sys.exit(1)
        print(f"🔒 Info: Custom Root CA will be injected: {root_ca_path}")
    else:
        if ldap_enabled:
            _flag_missing(
                "❌ Error: ROOT_CA_PATH must be set when ENABLE_LDAP=true."
            )
        elif api_egress_enabled:
            _flag_missing(
                "❌ Error: ROOT_CA_PATH must be set when using "
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS or "
                "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS."
            )
        else:
            # Ensure it's set to /dev/null if missing for compose interpolation
            os.environ["ROOT_CA_PATH"] = "/dev/null"

    # E. LDAP target validation
    ldap_target_ip = None
    if ldap_enabled:
        ldap_target_ip = _validate_rfc1918_private_ipv4("LDAP_TARGET_IP", env_str("LDAP_TARGET_IP"))
        if not ldap_target_ip:
            missing_config = True
        if not env_str("LDAP_TARGET_SNI"):
            print("❌ Error: ENABLE_LDAP is true but LDAP_TARGET_SNI is missing.")
            missing_config = True

    # F. Batch Client Mode: Additional Local API Validation (LLM + Embedding targets)
    def _validate_pinned_api_target(
        *,
        role_label: str,
        address_var: str,
        ip_var: str,
        sni_var: str,
    ) -> dict | None:
        target_address = env_str(address_var)
        if not target_address:
            return None

        target_has_error = False
        parsed = urlparse(target_address)

        if not batch_mode:
            print(f"❌ Error: {address_var} is set but BATCH_CLIENT_MODE_ON is false.")
            target_has_error = True

        if parsed.scheme != "https":
            print(f"❌ Error: {address_var} must start with https://")
            target_has_error = True

        if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
            print(
                f"❌ Error: {address_var} must be origin-only "
                "(https://host[:port]) without path/query/fragment."
            )
            target_has_error = True

        target_ip = _validate_rfc1918_private_ipv4(ip_var, env_str(ip_var))
        if not target_ip:
            target_has_error = True

        target_sni = env_str(sni_var)
        if not target_sni:
            host = parsed.hostname
            if host:
                target_sni = host
                os.environ[sni_var] = target_sni
            else:
                print(f"❌ Error: Could not derive SNI host from {address_var}.")
                target_has_error = True

        if target_has_error:
            return None

        # Rewrite the proxy target to the pinned IP to eliminate DNS dependence.
        # The SNI/Host header is controlled separately via *_SNI.
        netloc = target_ip
        if parsed.port:
            netloc = f"{target_ip}:{parsed.port}"
        rewritten = urlunparse(("https", netloc, "", "", "", ""))
        os.environ[address_var] = rewritten
        print(f"🔒 Info: Pinned {role_label} API egress target: {rewritten}")
        return {
            "address": rewritten,
            "ip": target_ip,
            "port": parsed.port or 443,
            "sni": target_sni,
        }

    explicit_toggle = env_bool("UKBGPT_API_EGRESS_TOGGLE_EXPLICIT")
    explicit_value_raw = env_str("UKBGPT_API_EGRESS_EXPLICIT_VALUE").lower()
    explicit_false = explicit_toggle and explicit_value_raw not in TRUE_VALUES
    explicit_true = explicit_toggle and explicit_value_raw in TRUE_VALUES

    llm_target = None
    embedding_target = None
    if api_egress_enabled:
        if not additional_api and not additional_embedding_api:
            _flag_missing(
                "❌ Error: ENABLE_API_EGRESS=true requires at least one additional upstream address "
                "(LLM and/or embedding)."
            )
        llm_target = _validate_pinned_api_target(
            role_label="LLM",
            address_var="BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS",
            ip_var="BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_IP",
            sni_var="BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_SNI",
        )
        embedding_target = _validate_pinned_api_target(
            role_label="embedding",
            address_var="BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS",
            ip_var="BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_IP",
            sni_var="BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_SNI",
        )

        if (additional_api and not llm_target) or (additional_embedding_api and not embedding_target):
            missing_config = True

        # Keep one api_egress template simple: if only one target is set, mirror it into the other role.
        if llm_target and not embedding_target:
            os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS"] = llm_target["address"]
            os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_IP"] = llm_target["ip"]
            os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_SNI"] = llm_target["sni"]
            embedding_target = dict(llm_target)
        elif embedding_target and not llm_target:
            os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS"] = embedding_target["address"]
            os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_IP"] = embedding_target["ip"]
            os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_SNI"] = embedding_target["sni"]
            llm_target = dict(embedding_target)

        os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ENABLED"] = "true" if llm_target else "false"
        os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ENABLED"] = (
            "true" if embedding_target else "false"
        )
    else:
        if additional_api or additional_embedding_api:
            if explicit_false:
                print(
                    "⚠️  [Feature] ENABLE_API_EGRESS is explicitly false. "
                    "Additional API address variables will be ignored."
                )
            elif not batch_mode:
                _flag_missing(
                    "❌ Error: Additional API address variables are set but BATCH_CLIENT_MODE_ON is false."
                )
            else:
                _flag_missing(
                    "❌ Error: Additional API address variables are set but API egress is disabled. "
                    "Set ENABLE_API_EGRESS=true or clear the address variables."
                )
        elif explicit_true and not batch_mode:
            _flag_missing("❌ Error: ENABLE_API_EGRESS=true requires BATCH_CLIENT_MODE_ON=true.")

        os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ENABLED"] = "false"
        os.environ["BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ENABLED"] = "false"

    firewall_egress_rules: list[tuple[str, str, int]] = []
    if ldap_target_ip:
        firewall_egress_rules.append(("tcp", ldap_target_ip, 636))
    for target in (llm_target, embedding_target):
        if target:
            firewall_egress_rules.append(("tcp", target["ip"], int(target["port"])))

    if firewall_egress_rules:
        os.environ["UKBGPT_FIREWALL_EGRESS_RULES"] = _encode_firewall_egress_rules(firewall_egress_rules)
        target_ips: list[str] = []
        for _, address, _ in firewall_egress_rules:
            if address not in target_ips:
                target_ips.append(address)
        os.environ["EGRESS_TARGET_IPS"] = ",".join(target_ips)
        os.environ["EGRESS_TARGET_IP"] = target_ips[0]
    else:
        os.environ.pop("UKBGPT_FIREWALL_EGRESS_RULES", None)
        os.environ.pop("EGRESS_TARGET_IPS", None)
        os.environ["EGRESS_TARGET_IP"] = ""

    # G. Optional dataset-root apps (internal-only utility containers)
    if dataset_structuring_enabled or cohort_feasibility_enabled:
        if not batch_mode:
            print(
                "❌ Error: ENABLE_DATASET_STRUCTURING_APP and ENABLE_COHORT_FEASIBILITY_APP "
                "require BATCH_CLIENT_MODE_ON=true."
            )
            missing_config = True

        data_root = env_str("DATASET_STRUCTURING_DATA_ROOT")
        if not data_root:
            print(
                "❌ Error: DATASET_STRUCTURING_DATA_ROOT must be set when "
                "ENABLE_DATASET_STRUCTURING_APP=true or ENABLE_COHORT_FEASIBILITY_APP=true."
            )
            missing_config = True
        else:
            resolved_data_root = os.path.abspath(os.path.expanduser(data_root))
            if not os.path.isdir(resolved_data_root):
                print(f"❌ Error: DATASET_STRUCTURING_DATA_ROOT is not a directory: {resolved_data_root}")
                missing_config = True
            else:
                os.environ["DATASET_STRUCTURING_DATA_ROOT"] = resolved_data_root
                os.environ.setdefault("COHORT_FEASIBILITY_DATA_ROOT", resolved_data_root)

        if dataset_structuring_enabled:
            # Dataset structuring requires explicit credentials to avoid accidental
            # fallback behavior and ambiguous runtime failures.
            for secret_var in ("API_KEY", "EMBEDDING_API_KEY"):
                if not env_str(secret_var):
                    print(f"❌ Error: {secret_var} must be set when ENABLE_DATASET_STRUCTURING_APP=true.")
                    missing_config = True

            _apply_dataset_structuring_resource_defaults()

    if icd10_enabled:
        if batch_mode:
            print("❌ Error: ENABLE_ICD_10_CODING_APP requires BATCH_CLIENT_MODE_ON=false.")
            missing_config = True

        if not env_str("MODEL_DEPLOYMENT_CONFIG"):
            print("❌ Error: MODEL_DEPLOYMENT_CONFIG must be set when ENABLE_ICD_10_CODING_APP=true.")
            missing_config = True

        if not env_str("EMBEDDING_MODEL_DEPLOYMENT_CONFIG"):
            print(
                "❌ Error: EMBEDDING_MODEL_DEPLOYMENT_CONFIG must be set when "
                "ENABLE_ICD_10_CODING_APP=true."
            )
            missing_config = True

        raw_icd10_data_root = env_str("ICD10_DATA_ROOT") or _default_icd10_data_root()
        resolved_icd10_data_root = _resolve_repo_or_absolute_path(raw_icd10_data_root)
        runtime_uid_raw = env_str("OPENWEBUI_RUNTIME_UID")
        runtime_gid_raw = env_str("OPENWEBUI_RUNTIME_GID")
        runtime_uid_int = int(runtime_uid_raw) if runtime_uid_raw.isdigit() else -1
        runtime_gid_int = int(runtime_gid_raw) if runtime_gid_raw.isdigit() else -1

        try:
            os.makedirs(resolved_icd10_data_root, exist_ok=True)
        except OSError as exc:
            print(
                "❌ Error: Could not create ICD10_DATA_ROOT "
                f"at {resolved_icd10_data_root}: {exc}"
            )
            missing_config = True
        else:
            if (
                not missing_config
                and runtime_uid_int > 0
                and runtime_gid_int > 0
                and not _has_dir_write_access_for_identity(
                    resolved_icd10_data_root, runtime_uid_int, runtime_gid_int
                )
            ):
                print(
                    "❌ Error: ICD10_DATA_ROOT is not writable/executable by ICD runtime user "
                    f"{runtime_uid_int}:{runtime_gid_int}: {resolved_icd10_data_root}"
                )
                missing_config = True
            else:
                ontology_path = os.path.join(
                    resolved_icd10_data_root,
                    "ICD-10-CODES_2025_structured.xlsx",
                )
                if not os.path.isfile(ontology_path):
                    print(f"❌ Error: ICD10 ontology workbook is missing: {ontology_path}")
                    missing_config = True
                else:
                    os.environ["ICD10_DATA_ROOT"] = resolved_icd10_data_root
                    _ensure_llm_structured_outputs_backend_xgrammar()

    if missing_config:
        print("Startup aborted due to missing configuration.")
        sys.exit(1)

    # Batch mode default ACL (localhost + docker internal only)
    if batch_mode:
        os.environ.setdefault("NGINX_ACL_ALLOW_LIST", "allow 127.0.0.1; allow 172.16.0.0/12;")
    return selection


def _resolve_ldap_feature(batch_mode: bool) -> bool:
    enable_ldap = env_bool("ENABLE_LDAP")
    if batch_mode and enable_ldap:
        print("⚠️  [Feature] LDAP requested, but Batch Client Mode is active. Disabling LDAP.")
        enable_ldap = False

    if enable_ldap:
        if not env_str("LDAP_APP_PASSWORD"):
            print("❌ Error: ENABLE_LDAP is true but LDAP_APP_PASSWORD is missing.")
            sys.exit(1)
        root_ca_path = env_str("ROOT_CA_PATH")
        if not root_ca_path or root_ca_path == "/dev/null":
            print("❌ Error: ENABLE_LDAP is true but ROOT_CA_PATH is missing.")
            sys.exit(1)
        if not env_str("LDAP_TARGET_SNI"):
            print("❌ Error: ENABLE_LDAP is true but LDAP_TARGET_SNI is missing.")
            sys.exit(1)
        if not _validate_rfc1918_private_ipv4("LDAP_TARGET_IP", env_str("LDAP_TARGET_IP")):
            sys.exit(1)
        print(f"🔹 [Feature] LDAP Integration: ENABLED (Target: {env_str('LDAP_TARGET_IP')})")
    else:
        print("🔸 [Feature] LDAP Integration: DISABLED")

    return enable_ldap


def _resolve_metrics_feature() -> bool:
    enable_metrics = env_bool("ENABLE_INTERNAL_METRICS")
    if enable_metrics:
        print("🔹 [Feature] Internal Metrics (Grafana): ENABLED")
    else:
        print("🔸 [Feature] Internal Metrics (Grafana): DISABLED")
    return enable_metrics


def _resolve_chat_purger_feature(batch_mode: bool) -> bool:
    # Chat purger feature:
    # - explicit ENABLE_CHAT_PURGER=true/false wins
    # - if not set, auto-enable when CHAT_HISTORY_RETENTION_DAYS is set
    enable_chat_purger_raw = os.getenv("ENABLE_CHAT_PURGER")
    if enable_chat_purger_raw is None:
        enable_chat_purger = bool(env_str("CHAT_HISTORY_RETENTION_DAYS"))
    else:
        enable_chat_purger = enable_chat_purger_raw.strip().lower() in TRUE_VALUES

    if batch_mode and enable_chat_purger:
        print("⚠️  [Feature] Chat Purger requested, but Batch Client Mode is active. Disabling Chat Purger.")
        enable_chat_purger = False

    if enable_chat_purger:
        # Keep parity with compose/features/chat_purger.yml default:
        # CHAT_HISTORY_RETENTION_DAYS=${CHAT_HISTORY_RETENTION_DAYS:-180}
        retention_days = env_str("CHAT_HISTORY_RETENTION_DAYS")
        if not retention_days:
            retention_days = "180"
            os.environ["CHAT_HISTORY_RETENTION_DAYS"] = retention_days

        if not retention_days.isdigit() or int(retention_days) <= 0:
            print("❌ Error: Chat Purger is enabled but CHAT_HISTORY_RETENTION_DAYS is missing or invalid (> 0 required).")
            sys.exit(1)

        interval = env_str("CHAT_HISTORY_PURGE_INTERVAL_SECONDS", "86400")
        if not interval.isdigit() or int(interval) <= 0:
            print("❌ Error: CHAT_HISTORY_PURGE_INTERVAL_SECONDS must be a positive integer.")
            sys.exit(1)

        print(
            "🔹 [Feature] Chat Purger: ENABLED "
            f"(Retention: {retention_days} days, Interval: {interval}s)"
        )
    else:
        print("🔸 [Feature] Chat Purger: DISABLED")

    return enable_chat_purger


def _resolve_api_egress_feature(
    batch_mode: bool,
    additional_llm_api: str,
    additional_embedding_api: str,
) -> bool:
    raw_toggle = os.getenv("ENABLE_API_EGRESS")
    if raw_toggle is None:
        enable_api_egress = batch_mode and bool(additional_llm_api or additional_embedding_api)
        if enable_api_egress:
            print(
                "⚠️  [Compat] ENABLE_API_EGRESS is unset. "
                "Inferring enabled state from additional API address variables."
            )
    else:
        enable_api_egress = raw_toggle.strip().lower() in TRUE_VALUES
        if enable_api_egress and not batch_mode:
            print("⚠️  [Feature] API egress requested, but Batch Client Mode is inactive. Disabling API egress.")
            enable_api_egress = False

    os.environ["ENABLE_API_EGRESS"] = "true" if enable_api_egress else "false"
    if enable_api_egress:
        print(
            "🔹 [Feature] Batch API Egress: ENABLED "
            f"(LLM: {additional_llm_api or 'off'}, Embedding: {additional_embedding_api or 'off'})"
        )
    else:
        print("🔸 [Feature] Batch API Egress: DISABLED")
    return enable_api_egress


def _resolve_dataset_structuring_feature(batch_mode: bool) -> bool:
    enabled = env_bool("ENABLE_DATASET_STRUCTURING_APP")
    if enabled:
        if not batch_mode:
            print("❌ Error: ENABLE_DATASET_STRUCTURING_APP requires batch client mode.")
            sys.exit(1)
        print("🔹 [Feature] Dataset Structuring App: ENABLED")
    else:
        print("🔸 [Feature] Dataset Structuring App: DISABLED")
    return enabled


def _resolve_cohort_feasibility_feature(batch_mode: bool) -> bool:
    enabled = env_bool("ENABLE_COHORT_FEASIBILITY_APP")
    if enabled:
        if not batch_mode:
            print("❌ Error: ENABLE_COHORT_FEASIBILITY_APP requires batch client mode.")
            sys.exit(1)
        print("🔹 [Feature] Cohort Feasibility App: ENABLED")
    else:
        print("🔸 [Feature] Cohort Feasibility App: DISABLED")
    return enabled


def _resolve_dictation_feature(batch_mode: bool, stt_deployment_config: str) -> bool:
    enabled = env_bool("ENABLE_DICTATION_APP")
    if not enabled:
        print("🔸 [Feature] Dictation App: DISABLED")
        return False

    if batch_mode:
        print("❌ Error: ENABLE_DICTATION_APP requires chatbot provider mode (BATCH_CLIENT_MODE_ON=false).")
        sys.exit(1)

    if not stt_deployment_config:
        print("❌ Error: ENABLE_DICTATION_APP=true requires STT_MODEL_DEPLOYMENT_CONFIG.")
        print(
            "   Action: export STT_MODEL_DEPLOYMENT_CONFIG="
            "\"examples/model_deployments/voxtral-mini-4b.single-gpu.toml\""
        )
        sys.exit(1)

    print("🔹 [Feature] Dictation App: ENABLED")
    return True


def _resolve_icd_10_coding_feature(
    batch_mode: bool,
    llm_deployment_config: str,
    embedding_deployment_config: str,
) -> bool:
    enabled = env_bool("ENABLE_ICD_10_CODING_APP")
    if not enabled:
        print("🔸 [Feature] ICD-10 Coding App: DISABLED")
        return False

    if batch_mode:
        print("❌ Error: ENABLE_ICD_10_CODING_APP requires chatbot provider mode (BATCH_CLIENT_MODE_ON=false).")
        sys.exit(1)

    if not llm_deployment_config:
        print("❌ Error: ENABLE_ICD_10_CODING_APP=true requires MODEL_DEPLOYMENT_CONFIG.")
        sys.exit(1)

    if not embedding_deployment_config:
        print("❌ Error: ENABLE_ICD_10_CODING_APP=true requires EMBEDDING_MODEL_DEPLOYMENT_CONFIG.")
        sys.exit(1)

    print("🔹 [Feature] ICD-10 Coding App: ENABLED")
    return True


def _validate_batch_direct_ports(batch_mode: bool) -> None:
    if not batch_mode:
        return

    start_raw = env_str("BATCH_CLIENT_DIRECT_PORT_START", "30001")
    end_raw = env_str("BATCH_CLIENT_DIRECT_PORT_END", "30032")
    batch_listen_raw = env_str("BATCH_CLIENT_LISTEN_PORT", "30000")

    if not start_raw.isdigit() or not end_raw.isdigit() or not batch_listen_raw.isdigit():
        print(
            "❌ Error: BATCH_CLIENT_DIRECT_PORT_START, "
            "BATCH_CLIENT_DIRECT_PORT_END, and BATCH_CLIENT_LISTEN_PORT must be numeric."
        )
        sys.exit(1)

    start_port = int(start_raw)
    end_port = int(end_raw)
    batch_listen_port = int(batch_listen_raw)
    if not (1 <= start_port <= 65535 and 1 <= end_port <= 65535):
        print("❌ Error: Direct worker port range must be within 1-65535.")
        sys.exit(1)
    if start_port > end_port:
        print("❌ Error: BATCH_CLIENT_DIRECT_PORT_START must be <= BATCH_CLIENT_DIRECT_PORT_END.")
        sys.exit(1)
    if start_port <= batch_listen_port <= end_port:
        print(
            "❌ Error: Direct worker port range overlaps "
            f"BATCH_CLIENT_LISTEN_PORT ({batch_listen_port})."
        )
        sys.exit(1)

    print(f"🔹 [Feature] Batch Direct Worker Ports: ENABLED by default in batch mode ({start_port}-{end_port})")


def assemble_compose_args(selection: SchemaRuntimeSelection | None = None) -> StartupConfig:
    """
    Builds startup compose/model selections and enabled core services.
    Matches the compose assembly logic in start.py.
    """
    if selection is None:
        selection = _resolve_schema_runtime_selection()
    batch_mode = selection.context.mode == "batch_client"

    compose_args = ["-f", os.path.join(ROOT_DIR, "compose/base.yml")]
    additional_llm_api = env_str("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS")
    additional_embedding_api = env_str("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS")

    if batch_mode:
        compose_args += ["-f", os.path.join(ROOT_DIR, "compose/modes/batch.client.yml")]
    else:
        compose_args += ["-f", os.path.join(ROOT_DIR, "compose/modes/frontend.provider.yml")]

    deployment_bundle = prepare_model_deployments(
        root_dir=ROOT_DIR,
        runtime_mode=selection.context.mode,
    )
    compose_args += deployment_bundle.compose_args

    enable_embedding_backend = bool(deployment_bundle.embedding_deployment_config)
    enable_stt_backend = bool(deployment_bundle.stt_deployment_config)
    enable_tts_backend = bool(deployment_bundle.tts_deployment_config)

    # Feature and app flags
    enable_rate_limiting = selection.enabled_features.get("rate_limiting", False)
    enable_ldap = selection.enabled_features.get("ldap", False)
    enable_metrics = selection.enabled_features.get("metrics", False)
    enable_chat_purger = selection.enabled_features.get("chat_purger", False)
    enable_api_egress = selection.enabled_features.get("api_egress", False)
    if enable_embedding_backend:
        print("🔹 [Feature] Embedding Backend: ENABLED")
    else:
        print("🔸 [Feature] Embedding Backend: DISABLED")
    if enable_stt_backend:
        print("🔹 [Feature] STT Backend: ENABLED")
    else:
        print("🔸 [Feature] STT Backend: DISABLED")
    if enable_tts_backend:
        print("🔹 [Feature] TTS Backend: ENABLED")
    else:
        print("🔸 [Feature] TTS Backend: DISABLED")
    if enable_rate_limiting:
        print("🔹 [Feature] Rate Limiting: ENABLED")
    else:
        print("🔸 [Feature] Rate Limiting: DISABLED")
    if enable_ldap:
        print(f"🔹 [Feature] LDAP Integration: ENABLED (Target: {env_str('LDAP_TARGET_IP')})")
    else:
        print("🔸 [Feature] LDAP Integration: DISABLED")
    if enable_metrics:
        print("🔹 [Feature] Internal Metrics (Grafana): ENABLED")
    else:
        print("🔸 [Feature] Internal Metrics (Grafana): DISABLED")
    if enable_chat_purger:
        print(
            "🔹 [Feature] Chat Purger: ENABLED "
            f"(Retention: {env_str('CHAT_HISTORY_RETENTION_DAYS')} days, "
            f"Interval: {env_str('CHAT_HISTORY_PURGE_INTERVAL_SECONDS')}s)"
        )
    else:
        print("🔸 [Feature] Chat Purger: DISABLED")
    if enable_api_egress:
        print(
            "🔹 [Feature] Batch API Egress: ENABLED "
            f"(LLM: {additional_llm_api or 'off'}, Embedding: {additional_embedding_api or 'off'})"
        )
    else:
        print("🔸 [Feature] Batch API Egress: DISABLED")
    enable_dataset_structuring_app = selection.enabled_apps.get("dataset_structuring", False)
    enable_cohort_feasibility_app = selection.enabled_apps.get("cohort_feasibility", False)
    enable_dictation_app = selection.enabled_apps.get("dictation", False)
    enable_icd10_app = selection.enabled_apps.get("icd_10_coding", False)
    if enable_dataset_structuring_app:
        print("🔹 [Feature] Dataset Structuring App: ENABLED")
    else:
        print("🔸 [Feature] Dataset Structuring App: DISABLED")
    if enable_cohort_feasibility_app:
        print("🔹 [Feature] Cohort Feasibility App: ENABLED")
    else:
        print("🔸 [Feature] Cohort Feasibility App: DISABLED")
    if enable_dictation_app:
        _resolve_dictation_feature(batch_mode, deployment_bundle.stt_deployment_config)
    else:
        print("🔸 [Feature] Dictation App: DISABLED")
    if enable_icd10_app:
        _resolve_icd_10_coding_feature(
            batch_mode,
            deployment_bundle.llm_deployment_config,
            deployment_bundle.embedding_deployment_config,
        )
    else:
        print("🔸 [Feature] ICD-10 Coding App: DISABLED")
    _validate_batch_direct_ports(batch_mode)

    if batch_mode:
        core_services = ["ingress"]
    else:
        core_services = ["frontend", "ingress"]

    enabled_feature_names = {
        name for name, enabled in selection.enabled_features.items() if enabled
    }
    enabled_app_names = {
        name for name, enabled in selection.enabled_apps.items() if enabled
    }

    schema = _load_runtime_env_schema()
    context = SelectionContext(
        mode="batch_client" if batch_mode else "chatbot_provider",
        enabled_features=frozenset(enabled_feature_names),
        enabled_apps=frozenset(enabled_app_names),
    )
    active_overlays = schema_selected_overlays(schema, context=context, env=os.environ)

    already_added = {
        compose_args[idx + 1]
        for idx, token in enumerate(compose_args[:-1])
        if token == "-f"
    }
    for overlay in active_overlays:
        compose_file = os.path.join(ROOT_DIR, overlay.compose_file)
        if compose_file in already_added:
            continue
        if not os.path.isfile(compose_file):
            print(f"❌ Error: Required compose overlay missing: {compose_file}")
            sys.exit(1)
        compose_args += ["-f", compose_file]
        already_added.add(compose_file)
        for service in overlay.services:
            if service and service not in core_services:
                core_services.append(service)

    extra_compose_files = env_str("UKBGPT_EXTRA_COMPOSE_FILES")
    if extra_compose_files:
        for entry in extra_compose_files.replace(";", ",").split(","):
            value = entry.strip()
            if not value:
                continue
            compose_file = value if os.path.isabs(value) else os.path.join(ROOT_DIR, value)
            compose_file = os.path.realpath(compose_file)
            if compose_file in already_added:
                continue
            if not os.path.isfile(compose_file):
                print(f"❌ Error: Extra compose overlay missing: {compose_file}")
                sys.exit(1)
            compose_args += ["-f", compose_file]
            already_added.add(compose_file)
        
    return StartupConfig(
        compose_args=compose_args,
        deployment_bundle=deployment_bundle,
        batch_mode=batch_mode,
        core_services=core_services,
    )


def prepare_startup_config() -> StartupConfig:
    selection = validate_environment()
    return assemble_compose_args(selection)

def _compose_services(compose_flags):
    if not compose_flags:
        return []

    cmd = ["docker", "compose", *compose_flags, "config", "--services"]
    res = run_command(cmd, capture_output=True)
    return [s.strip() for s in res.stdout.splitlines() if s.strip()]


def _discover_backend(
    *,
    label: str,
    services: list[str],
    worker_regex: str,
    router_name: str,
) -> BackendRoleDiscovery:
    if not services:
        return _empty_backend_discovery()

    pattern = re.compile(worker_regex)
    workers = []
    for service in services:
        match = pattern.match(service)
        if match:
            workers.append((int(match.group(1)), service))
    workers.sort(key=lambda item: item[0])
    worker_services = [service for _, service in workers]

    if not worker_services:
        print(f"❌ Error: No valid {label} worker services found in selected compose files.")
        print(f"   Expected names matching: {worker_regex}")
        sys.exit(1)

    has_router = router_name in services
    router_services = [router_name] if has_router else []
    backend_nodes = ",".join([f"{service}:5000" for service in worker_services])

    if has_router:
        endpoint = f"{router_name}:5000"
        bypass_router = "false"
        print(f"⚖️  [Discovery] {label}: router detected ({router_name}).")
    else:
        endpoint = f"{worker_services[0]}:5000"
        bypass_router = "true"
        print(
            f"🚀 [Discovery] {label}: no dedicated backend router service; "
            "ingress will route directly across workers."
        )

    print(f"🔍 [Discovery] {label}: {len(worker_services)} worker(s): {' '.join(worker_services)}")
    print(f"🔗 [Discovery] {label}: route {backend_nodes}")

    return BackendRoleDiscovery(
        workers=tuple(worker_services),
        router_services=tuple(router_services),
        backend_nodes=backend_nodes,
        endpoint=endpoint,
        bypass_router=bypass_router,
    )


def _empty_backend_discovery() -> BackendRoleDiscovery:
    return BackendRoleDiscovery()


def _split_semicolon_values(raw: str) -> list[str]:
    return [value.strip() for value in raw.split(";") if value.strip()]


def _resolved_model_id_for_role(resolved_deployments, role: str) -> str:
    for deployment in resolved_deployments:
        if deployment.role != role:
            continue
        for argument in deployment.family.runtime.command:
            if argument.startswith("--model="):
                return argument.split("=", 1)[1].strip()
        if role in {"stt", "tts"}:
            for argument in deployment.family.runtime.command:
                if not argument.startswith("--"):
                    return argument.strip()
        break
    return ""


def _resolved_deployment_for_role(resolved_deployments, role: str):
    for deployment in resolved_deployments:
        if deployment.role == role:
            return deployment
    return None


def _provider_api_base_url_for_stt(resolved_deployments, stt_endpoint: str) -> str:
    if not stt_endpoint:
        return ""
    deployment = _resolved_deployment_for_role(resolved_deployments, "stt")
    if deployment is None or not deployment.family.expose_in_provider_api:
        return ""
    return f"http://{stt_endpoint}/v1"


def _configure_frontend_openai_connections(
    *,
    openai_base_urls: list[str],
    primary_endpoint: str,
    enable_rate_limiting: bool,
) -> None:
    pipeline_url = "http://pipelines:9099/v1"
    configured_base_urls = _split_semicolon_values(env_str("OPENAI_API_BASE_URLS"))
    default_base_url = f"http://{primary_endpoint}/v1" if primary_endpoint else ""
    final_base_urls = configured_base_urls or list(openai_base_urls)
    if enable_rate_limiting:
        if not final_base_urls and default_base_url:
            final_base_urls = [default_base_url]
        if pipeline_url not in final_base_urls:
            final_base_urls.append(pipeline_url)
    else:
        final_base_urls = [value for value in final_base_urls if value != pipeline_url]
        if not final_base_urls and default_base_url:
            final_base_urls = [default_base_url]

    rendered_base_urls = ";".join(final_base_urls)
    if rendered_base_urls and rendered_base_urls != env_str("OPENAI_API_BASE_URLS"):
        os.environ["OPENAI_API_BASE_URLS"] = rendered_base_urls
        print(f"🔗 [Discovery] Frontend OPENAI_API_BASE_URLS={rendered_base_urls}")

    configured_api_keys = _split_semicolon_values(env_str("OPENAI_API_KEYS"))
    pipeline_api_key = env_str("OPENWEBUI_PIPELINES_API_KEY", "0p3n-w3bu!")
    non_pipeline_urls = [url for url in final_base_urls if url != pipeline_url]
    non_pipeline_keys = [key for key in configured_api_keys if key != pipeline_api_key]
    if len(non_pipeline_keys) < len(non_pipeline_urls):
        non_pipeline_keys.extend(["Empty"] * (len(non_pipeline_urls) - len(non_pipeline_keys)))
    else:
        non_pipeline_keys = non_pipeline_keys[: len(non_pipeline_urls)]

    final_api_keys = list(non_pipeline_keys)
    if enable_rate_limiting and pipeline_url in final_base_urls:
        final_api_keys.append(pipeline_api_key)

    rendered_api_keys = ";".join(final_api_keys)
    if rendered_api_keys != env_str("OPENAI_API_KEYS"):
        os.environ["OPENAI_API_KEYS"] = rendered_api_keys
        print("🔗 [Discovery] Frontend OPENAI_API_KEYS updated for configured provider URLs")


def discover_backends(
    llm_compose_flags: list[str],
    embedding_compose_flags: list[str],
    stt_compose_flags: list[str],
    tts_compose_flags: list[str],
    resolved_deployments=(),
) -> BackendDiscovery:
    """
    Discovers chat, embedding, and STT backend services and exports runtime env wiring.
    """
    llm_services = _compose_services(llm_compose_flags)
    embedding_services = _compose_services(embedding_compose_flags)
    stt_services = _compose_services(stt_compose_flags)
    tts_services = _compose_services(tts_compose_flags)

    llm = (
        _discover_backend(
            label="LLM",
            services=llm_services,
            worker_regex=r"^worker_(\d+)$",
            router_name="backend_router",
        )
        if llm_services
        else _empty_backend_discovery()
    )

    embedding = (
        _discover_backend(
            label="Embedding",
            services=embedding_services,
            worker_regex=r"^embedding_worker_(\d+)$",
            router_name="embedding_backend_router",
        )
        if embedding_services
        else _empty_backend_discovery()
    )

    stt = (
        _discover_backend(
            label="STT",
            services=stt_services,
            worker_regex=r"^stt_worker_(\d+)$",
            router_name="stt_backend_router",
        )
        if stt_services
        else _empty_backend_discovery()
    )
    tts = (
        _discover_backend(
            label="TTS",
            services=tts_services,
            worker_regex=r"^tts_worker_(\d+)$",
            router_name="tts_backend_router",
        )
        if tts_services
        else _empty_backend_discovery()
    )

    if not llm.workers and not embedding.workers:
        batch_mode = env_bool("BATCH_CLIENT_MODE_ON")
        dictation_enabled = env_bool("ENABLE_DICTATION_APP")
        if dictation_enabled and stt.workers:
            if tts.workers:
                print("ℹ️  [Discovery] STT/TTS-only backend mode enabled for dictation.")
            else:
                print("ℹ️  [Discovery] STT-only backend mode enabled for dictation.")
            print("   LLM/embedding workers are disabled by configuration.")
        elif batch_mode and tts.workers:
            print("ℹ️  [Discovery] TTS-only backend mode enabled for batch usage.")
            print("   LLM/embedding workers are disabled by configuration.")
        else:
            print("❌ Error: No LLM, embedding, or allowed fallback backend workers discovered.")
            if stt.workers and not dictation_enabled:
                print("   STT-only mode is supported only when ENABLE_DICTATION_APP=true.")
            if tts.workers and not batch_mode:
                print("   TTS-only mode is supported only in batch client mode.")
            print(
                "   Action: keep MODEL_DEPLOYMENT_CONFIG and/or "
                "EMBEDDING_MODEL_DEPLOYMENT_CONFIG set."
            )
            print(
                "   STT and TTS remain optional and are enabled via "
                "STT_MODEL_DEPLOYMENT_CONFIG and TTS_MODEL_DEPLOYMENT_CONFIG."
            )
            sys.exit(1)

    os.environ["LLM_BACKEND_NODES"] = llm.backend_nodes
    os.environ["EMBEDDING_BACKEND_NODES"] = embedding.backend_nodes
    os.environ["STT_BACKEND_NODES"] = stt.backend_nodes
    os.environ["TTS_BACKEND_NODES"] = tts.backend_nodes
    os.environ["LLM_BYPASS_ROUTER"] = llm.bypass_router
    os.environ["EMBEDDING_BYPASS_ROUTER"] = embedding.bypass_router
    os.environ["STT_BYPASS_ROUTER"] = stt.bypass_router
    os.environ["TTS_BYPASS_ROUTER"] = tts.bypass_router
    os.environ["LLM_ENDPOINT"] = llm.endpoint
    os.environ["EMBEDDING_ENDPOINT"] = embedding.endpoint
    os.environ["STT_ENDPOINT"] = stt.endpoint
    os.environ["TTS_ENDPOINT"] = tts.endpoint

    # Backward compatibility for existing templates/features.
    fallback_nodes = llm.backend_nodes or embedding.backend_nodes
    fallback_bypass = llm.bypass_router if llm.workers else embedding.bypass_router
    primary_endpoint = llm.endpoint or embedding.endpoint
    if not primary_endpoint and env_bool("ENABLE_DICTATION_APP") and stt.endpoint:
        # Dictation STT-only mode still needs a valid OpenAI endpoint in frontend env wiring.
        fallback_nodes = stt.backend_nodes
        fallback_bypass = stt.bypass_router
        primary_endpoint = stt.endpoint
    elif not primary_endpoint and env_bool("BATCH_CLIENT_MODE_ON") and tts.endpoint:
        fallback_nodes = tts.backend_nodes
        fallback_bypass = tts.bypass_router
        primary_endpoint = tts.endpoint
    os.environ["BACKEND_NODES"] = fallback_nodes
    os.environ["BYPASS_ROUTER"] = fallback_bypass
    os.environ["VLLM_ENDPOINT"] = primary_endpoint
    os.environ["PRIMARY_OPENAI_ENDPOINT"] = primary_endpoint

    llm_model_id = env_str("LLM_MODEL_ID") or _resolved_model_id_for_role(resolved_deployments, "llm")
    if llm_model_id and not env_str("LLM_MODEL_ID"):
        os.environ["LLM_MODEL_ID"] = llm_model_id

    embedding_model_id = env_str("EMBEDDING_MODEL_ID") or _resolved_model_id_for_role(
        resolved_deployments, "embedding"
    )
    if embedding_model_id and not env_str("EMBEDDING_MODEL_ID"):
        os.environ["EMBEDDING_MODEL_ID"] = embedding_model_id

    openai_base_urls = []
    if llm.endpoint:
        openai_base_urls.append(f"http://{llm.endpoint}/v1")
        print(
            "🔗 [Discovery] OpenWebUI provider API LLM backend: "
            f"http://{llm.endpoint}/v1"
        )
    stt_provider_api_url = _provider_api_base_url_for_stt(resolved_deployments, stt.endpoint)
    if stt_provider_api_url and stt.endpoint != llm.endpoint:
        openai_base_urls.append(stt_provider_api_url)
        print(
            "🔗 [Discovery] OpenWebUI provider API multimodal STT backend: "
            f"{stt_provider_api_url} "
            "(exposed via /api/models for fast multimodal chat)"
        )
    if embedding.endpoint and embedding.endpoint != llm.endpoint:
        openai_base_urls.append(f"http://{embedding.endpoint}/v1")
        print(
            "🔗 [Discovery] OpenWebUI provider API embedding backend: "
            f"http://{embedding.endpoint}/v1 "
            "(exposed via /api/models and /api/embeddings)"
        )
    _configure_frontend_openai_connections(
        openai_base_urls=openai_base_urls,
        primary_endpoint=primary_endpoint,
        enable_rate_limiting=env_bool("ENABLE_RATE_LIMITING"),
    )

    # If embedding backend exists, wire OpenWebUI's internal RAG embeddings to it.
    if embedding.endpoint:
        if not env_str("RAG_EMBEDDING_ENGINE"):
            os.environ["RAG_EMBEDDING_ENGINE"] = "openai"
        if not env_str("RAG_OPENAI_API_BASE_URL"):
            os.environ["RAG_OPENAI_API_BASE_URL"] = f"http://{embedding.endpoint}/v1"
        embedding_model_id = (
            env_str("EMBEDDING_MODEL_ID")
            or _resolved_model_id_for_role(resolved_deployments, "embedding")
            or "Alibaba-NLP/gte-Qwen2-1.5B-instruct"
        )
        if not env_str("RAG_EMBEDDING_MODEL"):
            os.environ["RAG_EMBEDDING_MODEL"] = embedding_model_id
        print(
            "🔗 [Discovery] OpenWebUI internal RAG embeddings: "
            f"{os.environ['RAG_OPENAI_API_BASE_URL']} ({os.environ['RAG_EMBEDDING_MODEL']})"
        )

    # STT intentionally remains on the dedicated audio env vars. OpenWebUI's
    # transcription integration is configured via AUDIO_STT_* rather than the
    # generic OPENAI_API_BASE_URLS provider list.
    if stt.endpoint:
        if not env_str("AUDIO_STT_ENGINE"):
            os.environ["AUDIO_STT_ENGINE"] = "openai"
        if not env_str("AUDIO_STT_OPENAI_API_BASE_URL"):
            os.environ["AUDIO_STT_OPENAI_API_BASE_URL"] = f"http://{stt.endpoint}/v1"
        stt_model_id = (
            env_str("STT_MODEL_ID")
            or _resolved_model_id_for_role(resolved_deployments, "stt")
            or "mistralai/Voxtral-Mini-4B-Realtime-2602"
        )
        if not env_str("AUDIO_STT_MODEL"):
            os.environ["AUDIO_STT_MODEL"] = stt_model_id
        print(
            "🔗 [Discovery] OpenWebUI dedicated STT backend: "
            f"{os.environ['AUDIO_STT_OPENAI_API_BASE_URL']} ({os.environ['AUDIO_STT_MODEL']})"
        )
        if env_bool("ENABLE_DICTATION_APP") and not env_str("DICTATION_LLM_BASE_URL") and stt_provider_api_url:
            os.environ["DICTATION_LLM_BASE_URL"] = stt_provider_api_url
            print(
                "🔗 [Discovery] Dictation translation backend defaulted to multimodal STT endpoint: "
                f"{stt_provider_api_url}"
            )

    if tts.endpoint:
        tts_model_id = (
            env_str("TTS_MODEL_ID")
            or _resolved_model_id_for_role(resolved_deployments, "tts")
            or "mistralai/Voxtral-4B-TTS-2603"
        )
        if not env_str("TTS_MODEL_ID"):
            os.environ["TTS_MODEL_ID"] = tts_model_id
        print(
            "🔗 [Discovery] Internal TTS backend: "
            f"http://{tts.endpoint}/v1 ({tts_model_id})"
        )

    return BackendDiscovery(
        llm=llm,
        embedding=embedding,
        stt=stt,
        tts=tts,
    )
