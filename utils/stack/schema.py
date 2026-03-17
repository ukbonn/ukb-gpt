import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - exercised on older Python
    import tomli as tomllib  # type: ignore


TRUE_VALUES = {"1", "true", "yes", "on"}

OVERLAY_SCHEMA_REL_PATH = "compose/schema.toml"
OVERLAY_COMPOSE_SUBDIRS = ("modes", "features", "apps")


@dataclass(frozen=True)
class VariableSpec:
    var_id: str
    type: str
    default: str | None
    secret: bool
    description: str
    examples: tuple[str, ...]
    validators: tuple[str, ...]


@dataclass(frozen=True)
class VariableRef:
    var_id: str
    prompt_order: int
    prompt: bool
    include_in_env_file: bool
    description: str
    examples: tuple[str, ...]
    required_when_all: tuple[str, ...]
    required_when_any: tuple[str, ...]
    applicable_when_all: tuple[str, ...]
    applicable_when_any: tuple[str, ...]


@dataclass(frozen=True)
class EffectiveVariable:
    ref: VariableRef
    spec: VariableSpec
    description: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class OverlaySpec:
    overlay_id: str
    name: str
    kind: str
    order: int
    compose_file: str
    toml_file: str
    title: str
    summary: str
    doc_path: str
    availability_modes: tuple[str, ...]
    toggle_var: str
    wizard_enabled: bool
    services: tuple[str, ...]
    compatible_features: tuple[str, ...]
    compatible_apps: tuple[str, ...]
    auto_enable_when_any: tuple[str, ...]
    use_when: tuple[str, ...]
    behavior: tuple[str, ...]
    verify: tuple[str, ...]
    access: tuple[str, ...]
    variables: tuple[VariableRef, ...]


@dataclass(frozen=True)
class ModelArchitecturePresetSpec:
    architecture_id: str
    environment: dict[str, str]
    build_args: dict[str, str]
    command_append: tuple[str, ...]
    shm_size: str
    extra_volumes: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class ModelRuntimeSpec:
    command: tuple[str, ...]
    environment: dict[str, str]
    build_args: dict[str, str]
    default_vllm_openai_image: str
    shm_size: str
    extra_volumes: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class ModelFamilySpec:
    family_id: str
    role: str
    title: str
    summary: str
    base_compose_file: str
    base_service: str
    order: int
    wizard_enabled: bool
    accelerator: str
    supports_expert_parallel: bool
    toml_file: str
    runtime: ModelRuntimeSpec
    architectures: dict[str, ModelArchitecturePresetSpec]
    variables: tuple[VariableRef, ...]


@dataclass(frozen=True)
class SelectionContext:
    mode: str
    enabled_features: frozenset[str]
    enabled_apps: frozenset[str]


@dataclass(frozen=True)
class EnvSchema:
    root_dir: str
    catalog: dict[str, VariableSpec]
    overlays: dict[str, OverlaySpec]
    model_families: dict[str, ModelFamilySpec]

    def by_kind(self, kind: str) -> list[OverlaySpec]:
        return sorted(
            [overlay for overlay in self.overlays.values() if overlay.kind == kind],
            key=lambda item: (item.order, item.name),
        )

    def mode_overlay(self, mode_name: str) -> OverlaySpec | None:
        for overlay in self.by_kind("mode"):
            if overlay.name == mode_name:
                return overlay
        return None

    def feature_overlay(self, feature_name: str) -> OverlaySpec | None:
        for overlay in self.by_kind("feature"):
            if overlay.name == feature_name:
                return overlay
        return None

    def app_overlay(self, app_name: str) -> OverlaySpec | None:
        for overlay in self.by_kind("app"):
            if overlay.name == app_name:
                return overlay
        return None

    def model_families_by_role(self, role: str, *, wizard_only: bool = False) -> list[ModelFamilySpec]:
        families = [family for family in self.model_families.values() if family.role == role]
        if wizard_only:
            families = [family for family in families if family.wizard_enabled]
        return sorted(families, key=lambda item: (item.order, item.title, item.family_id))


_ALLOWED_MODEL_ARCHITECTURES = {
    "default",
    "nvidia_ampere",
    "nvidia_hopper",
    "nvidia_blackwell",
}


def parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


def _as_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if isinstance(item, str):
                out.append(item)
        return out
    return []


def _clean_list(values: list[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values:
        stripped = value.strip()
        if stripped:
            cleaned.append(stripped)
    return tuple(cleaned)


def _load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        parsed = tomllib.load(handle)
        if isinstance(parsed, dict):
            return parsed
        return {}


def _parse_catalog(catalog_data: dict, catalog_path: Path) -> dict[str, VariableSpec]:
    variables = catalog_data.get("vars")
    if not isinstance(variables, dict):
        raise ValueError(f"Invalid schema TOML (missing [vars]) at {catalog_path}")

    parsed: dict[str, VariableSpec] = {}
    for var_id, entry in variables.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid variable definition for {var_id} in {catalog_path}")
        if "type" not in entry:
            raise ValueError(
                f"Invalid variable definition for {var_id} in {catalog_path}: missing type"
            )

        var_type = str(entry.get("type", "string"))
        default_raw = entry.get("default")
        default_value = None if default_raw is None else str(default_raw)
        secret = bool(entry.get("secret", False))
        description = str(entry.get("description", "")).strip()
        examples = tuple(_as_list(entry.get("examples")))
        validators = tuple(_as_list(entry.get("validators")))

        parsed[var_id] = VariableSpec(
            var_id=var_id,
            type=var_type,
            default=default_value,
            secret=secret,
            description=description,
            examples=examples,
            validators=validators,
        )
    return parsed


def _parse_overlay(
    overlay_data: dict,
    *,
    schema_path: Path,
    root_dir: str,
) -> OverlaySpec:
    if not isinstance(overlay_data, dict):
        raise ValueError(f"Invalid [[overlays]] entry in {schema_path}")

    overlay_id = str(overlay_data.get("id", "")).strip()
    name = str(overlay_data.get("name", "")).strip()
    kind = str(overlay_data.get("kind", "")).strip()
    order = int(overlay_data.get("order", 1000))
    compose_file = str(overlay_data.get("compose_file", "")).strip()
    title = str(overlay_data.get("title", "")).strip()
    summary = str(overlay_data.get("summary", "")).strip()
    doc_path = str(overlay_data.get("doc_path", "")).strip()
    availability_modes = tuple(_as_list(overlay_data.get("availability_modes")))
    toggle_var = str(overlay_data.get("toggle_var", "")).strip()
    wizard_enabled = bool(overlay_data.get("wizard_enabled", True))
    services = tuple(_as_list(overlay_data.get("services")))
    compatible_features = tuple(_as_list(overlay_data.get("compatible_features")))
    compatible_apps = tuple(_as_list(overlay_data.get("compatible_apps")))
    auto_enable_when_any = tuple(_as_list(overlay_data.get("auto_enable_when_any")))
    use_when = tuple(_as_list(overlay_data.get("use_when")))
    behavior = tuple(_as_list(overlay_data.get("behavior")))
    verify = tuple(_as_list(overlay_data.get("verify")))
    access = tuple(_as_list(overlay_data.get("access")))

    if not overlay_id:
        raise ValueError(f"Missing overlay.id in {schema_path}")
    if not name:
        raise ValueError(f"Missing overlay.name in {schema_path}")
    if kind not in {"base", "mode", "feature", "app"}:
        raise ValueError(f"Invalid overlay.kind '{kind}' in {schema_path}")
    if not compose_file:
        raise ValueError(f"Missing overlay.compose_file in {schema_path}")
    if kind in {"mode", "feature", "app"} and not doc_path:
        raise ValueError(f"Missing overlay.doc_path for {overlay_id} in {schema_path}")

    resolved_compose_path = os.path.join(root_dir, compose_file)
    if not os.path.isfile(resolved_compose_path):
        raise ValueError(
            f"Overlay {overlay_id} references missing compose file: {compose_file}"
        )

    return OverlaySpec(
        overlay_id=overlay_id,
        name=name,
        kind=kind,
        order=order,
        compose_file=compose_file,
        toml_file=os.path.relpath(schema_path, root_dir),
        title=title,
        summary=summary,
        doc_path=doc_path,
        availability_modes=availability_modes,
        toggle_var=toggle_var,
        wizard_enabled=wizard_enabled,
        services=services,
        compatible_features=compatible_features,
        compatible_apps=compatible_apps,
        auto_enable_when_any=auto_enable_when_any,
        use_when=use_when,
        behavior=behavior,
        verify=verify,
        access=access,
        variables=(),
    )


def _build_variable_ref(
    var_id: str,
    raw_ref: dict,
    *,
    parent_path: Path,
    parent_label: str,
    catalog: dict[str, VariableSpec],
) -> VariableRef:
    if not var_id:
        raise ValueError(f"Missing variable id in {parent_path}")
    if var_id not in catalog:
        raise ValueError(f"{parent_label} references unknown catalog variable '{var_id}'")

    raw_examples = raw_ref.get("examples")
    if raw_examples is None and "example" in raw_ref:
        raw_examples = raw_ref.get("example")
    ref_description = str(raw_ref.get("description", "")).strip()
    ref_examples = _clean_list(_as_list(raw_examples))
    prompt = bool(raw_ref.get("prompt", True))
    effective_description = ref_description or catalog[var_id].description.strip()
    if prompt and not effective_description:
        raise ValueError(
            f"Prompted variable '{var_id}' in {parent_path} must define a description "
            "in compose/schema.toml or the referring model metadata TOML."
        )
    return VariableRef(
        var_id=var_id,
        prompt_order=int(raw_ref.get("prompt_order", 1000)),
        prompt=prompt,
        include_in_env_file=bool(raw_ref.get("include_in_env_file", True)),
        description=ref_description,
        examples=ref_examples,
        required_when_all=tuple(_as_list(raw_ref.get("required_when_all"))),
        required_when_any=tuple(_as_list(raw_ref.get("required_when_any"))),
        applicable_when_all=tuple(_as_list(raw_ref.get("applicable_when_all"))),
        applicable_when_any=tuple(_as_list(raw_ref.get("applicable_when_any"))),
    )


def _parse_variable_refs(
    raw_var_refs: object,
    *,
    parent_path: Path,
    parent_label: str,
    catalog: dict[str, VariableSpec],
) -> tuple[VariableRef, ...]:
    if not isinstance(raw_var_refs, list):
        raise ValueError(f"Invalid [[variables]] section in {parent_path}")

    seen_var_refs: set[str] = set()
    variable_refs: list[VariableRef] = []
    for raw_ref in raw_var_refs:
        if not isinstance(raw_ref, dict):
            raise ValueError(f"Invalid variable reference in {parent_path}")

        var_id = str(raw_ref.get("id", "")).strip()
        if var_id in seen_var_refs:
            raise ValueError(f"Duplicate variable id '{var_id}' in {parent_path}")

        seen_var_refs.add(var_id)
        variable_refs.append(
            _build_variable_ref(
                var_id,
                raw_ref,
                parent_path=parent_path,
                parent_label=parent_label,
                catalog=catalog,
            )
        )

    return tuple(variable_refs)


def _parse_schema_bindings(
    schema_data: dict,
    *,
    schema_path: Path,
    catalog: dict[str, VariableSpec],
    overlays: Mapping[str, OverlaySpec],
) -> dict[str, tuple[VariableRef, ...]]:
    variables = schema_data.get("vars")
    if not isinstance(variables, dict):
        raise ValueError(f"Invalid schema TOML (missing [vars]) at {schema_path}")

    refs_by_overlay: dict[str, list[VariableRef]] = {overlay_id: [] for overlay_id in overlays}
    seen_bindings: set[tuple[str, str]] = set()
    for var_id, entry in variables.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid variable definition for {var_id} in {schema_path}")

        raw_bindings = entry.get("bindings", [])
        if not isinstance(raw_bindings, list):
            raise ValueError(f"Invalid [[vars.{var_id}.bindings]] section in {schema_path}")

        for raw_binding in raw_bindings:
            if not isinstance(raw_binding, dict):
                raise ValueError(f"Invalid binding for variable {var_id} in {schema_path}")

            overlay_id = str(raw_binding.get("overlay", "")).strip()
            if not overlay_id:
                raise ValueError(f"Missing binding.overlay for variable '{var_id}' in {schema_path}")
            if overlay_id not in overlays:
                raise ValueError(
                    f"Variable '{var_id}' binding references unknown overlay '{overlay_id}'"
                )

            binding_key = (overlay_id, var_id)
            if binding_key in seen_bindings:
                raise ValueError(
                    f"Duplicate binding for variable '{var_id}' in overlay '{overlay_id}'"
                )
            seen_bindings.add(binding_key)

            refs_by_overlay[overlay_id].append(
                _build_variable_ref(
                    var_id,
                    raw_binding,
                    parent_path=schema_path,
                    parent_label=f"Variable '{var_id}' binding",
                    catalog=catalog,
                )
            )

    return {
        overlay_id: tuple(sorted(refs, key=lambda item: (item.prompt_order, item.var_id)))
        for overlay_id, refs in refs_by_overlay.items()
    }


def _parse_model_family(
    model_data: dict,
    *,
    model_path: Path,
    root_dir: str,
    catalog: dict[str, VariableSpec],
) -> ModelFamilySpec:
    model = model_data.get("model")
    if not isinstance(model, dict):
        raise ValueError(f"Missing [model] table in {model_path}")

    family_id = str(model.get("id", "")).strip()
    role = str(model.get("role", "")).strip()
    title = str(model.get("title", "")).strip()
    summary = str(model.get("summary", "")).strip()
    base_compose_file = str(model.get("base_compose_file", "")).strip()
    base_service = str(model.get("base_service", "")).strip()
    order = int(model.get("order", 1000))
    wizard_enabled = bool(model.get("wizard_enabled", True))
    accelerator = str(model.get("accelerator", "nvidia")).strip()
    supports_expert_parallel = bool(model.get("supports_expert_parallel", False))

    if not family_id:
        raise ValueError(f"Missing model.id in {model_path}")
    if role not in {"llm", "embedding", "stt"}:
        raise ValueError(f"Invalid model.role '{role}' in {model_path}")
    if not title:
        raise ValueError(f"Missing model.title in {model_path}")
    if not base_compose_file:
        raise ValueError(f"Missing model.base_compose_file in {model_path}")
    if not base_service:
        raise ValueError(f"Missing model.base_service in {model_path}")
    if accelerator not in {"nvidia", "none"}:
        raise ValueError(f"Invalid model.accelerator '{accelerator}' in {model_path}")

    resolved_base_compose = os.path.join(root_dir, base_compose_file)
    if not os.path.isfile(resolved_base_compose):
        raise ValueError(
            f"Model family {family_id} references missing base compose file: {base_compose_file}"
        )

    raw_runtime = model_data.get("runtime")
    if not isinstance(raw_runtime, dict):
        raise ValueError(f"Model family {family_id} must define a [runtime] table in {model_path}")

    raw_runtime_command = raw_runtime.get("command", [])
    raw_runtime_environment = raw_runtime.get("environment", {})
    raw_runtime_build_args = raw_runtime.get("build_args", {})
    raw_runtime_extra_volumes = raw_runtime.get("extra_volumes", [])

    if raw_runtime_command is None:
        raw_runtime_command = []
    if raw_runtime_environment is None:
        raw_runtime_environment = {}
    if raw_runtime_build_args is None:
        raw_runtime_build_args = {}
    if raw_runtime_extra_volumes is None:
        raw_runtime_extra_volumes = []

    if not isinstance(raw_runtime_command, list):
        raise ValueError(f"Model family {family_id} runtime.command must be a list in {model_path}")
    if not isinstance(raw_runtime_environment, dict):
        raise ValueError(
            f"Model family {family_id} runtime.environment must be a map in {model_path}"
        )
    if not isinstance(raw_runtime_build_args, dict):
        raise ValueError(
            f"Model family {family_id} runtime.build_args must be a map in {model_path}"
        )
    if not isinstance(raw_runtime_extra_volumes, list):
        raise ValueError(
            f"Model family {family_id} runtime.extra_volumes must be a list in {model_path}"
        )

    runtime_extra_volumes: list[dict[str, object]] = []
    for raw_volume in raw_runtime_extra_volumes:
        if not isinstance(raw_volume, dict):
            raise ValueError(
                f"Model family {family_id} runtime.extra_volumes entries must be tables in {model_path}"
            )
        volume: dict[str, object] = {}
        for key, value in raw_volume.items():
            volume[str(key)] = value
        runtime_extra_volumes.append(volume)

    runtime = ModelRuntimeSpec(
        command=tuple(str(item).strip() for item in raw_runtime_command if str(item).strip()),
        environment={
            str(key).strip(): str(value)
            for key, value in raw_runtime_environment.items()
            if str(key).strip()
        },
        build_args={
            str(key).strip(): str(value)
            for key, value in raw_runtime_build_args.items()
            if str(key).strip()
        },
        default_vllm_openai_image=str(raw_runtime.get("default_vllm_openai_image", "")).strip(),
        shm_size=str(raw_runtime.get("shm_size", "")).strip(),
        extra_volumes=tuple(runtime_extra_volumes),
    )

    raw_architectures = model_data.get("architectures")
    if not isinstance(raw_architectures, dict) or "default" not in raw_architectures:
        raise ValueError(
            f"Model family {family_id} must define [architectures.default] in {model_path}"
        )

    architectures: dict[str, ModelArchitecturePresetSpec] = {}
    for architecture_id, raw_architecture in raw_architectures.items():
        if architecture_id not in _ALLOWED_MODEL_ARCHITECTURES:
            raise ValueError(
                f"Model family {family_id} uses invalid architecture preset '{architecture_id}'"
            )
        if not isinstance(raw_architecture, dict):
            raise ValueError(
                f"Model family {family_id} has invalid architecture preset '{architecture_id}'"
            )

        raw_environment = raw_architecture.get("environment", {})
        raw_build_args = raw_architecture.get("build_args", {})
        raw_command_append = raw_architecture.get("command_append", [])
        raw_extra_volumes = raw_architecture.get("extra_volumes", [])

        if raw_environment is None:
            raw_environment = {}
        if raw_build_args is None:
            raw_build_args = {}
        if raw_command_append is None:
            raw_command_append = []
        if raw_extra_volumes is None:
            raw_extra_volumes = []

        if not isinstance(raw_environment, dict):
            raise ValueError(
                f"Model family {family_id} architecture '{architecture_id}' has invalid environment map"
            )
        if not isinstance(raw_build_args, dict):
            raise ValueError(
                f"Model family {family_id} architecture '{architecture_id}' has invalid build_args map"
            )
        if not isinstance(raw_command_append, list):
            raise ValueError(
                f"Model family {family_id} architecture '{architecture_id}' has invalid command_append"
            )
        if not isinstance(raw_extra_volumes, list):
            raise ValueError(
                f"Model family {family_id} architecture '{architecture_id}' has invalid extra_volumes"
            )

        environment = {
            str(key).strip(): str(value)
            for key, value in raw_environment.items()
            if str(key).strip()
        }
        build_args = {
            str(key).strip(): str(value)
            for key, value in raw_build_args.items()
            if str(key).strip()
        }
        command_append = tuple(str(item).strip() for item in raw_command_append if str(item).strip())

        extra_volumes: list[dict[str, object]] = []
        for raw_volume in raw_extra_volumes:
            if not isinstance(raw_volume, dict):
                raise ValueError(
                    f"Model family {family_id} architecture '{architecture_id}' has invalid extra volume entry"
                )
            volume: dict[str, object] = {}
            for key, value in raw_volume.items():
                volume[str(key)] = value
            extra_volumes.append(volume)

        architectures[architecture_id] = ModelArchitecturePresetSpec(
            architecture_id=architecture_id,
            environment=environment,
            build_args=build_args,
            command_append=command_append,
            shm_size=str(raw_architecture.get("shm_size", "")).strip(),
            extra_volumes=tuple(extra_volumes),
        )

    variable_refs = _parse_variable_refs(
        model_data.get("variables", []),
        parent_path=model_path,
        parent_label=f"Model family {family_id}",
        catalog=catalog,
    )

    return ModelFamilySpec(
        family_id=family_id,
        role=role,
        title=title,
        summary=summary,
        base_compose_file=base_compose_file,
        base_service=base_service,
        order=order,
        wizard_enabled=wizard_enabled,
        accelerator=accelerator,
        supports_expert_parallel=supports_expert_parallel,
        toml_file=os.path.relpath(model_path, root_dir),
        runtime=runtime,
        architectures=architectures,
        variables=variable_refs,
    )


def _expected_overlay_compose_files(root_dir: str) -> set[str]:
    compose_dir = Path(root_dir) / "compose"
    expected: set[str] = set()

    base_compose = compose_dir / "base.yml"
    if not base_compose.is_file():
        raise ValueError(f"Missing overlay compose file: {os.path.relpath(base_compose, root_dir)}")
    expected.add(os.path.relpath(base_compose, root_dir))

    for subdir_name in OVERLAY_COMPOSE_SUBDIRS:
        subdir = compose_dir / subdir_name
        if not subdir.is_dir():
            raise ValueError(f"Overlay compose directory missing: {subdir}")
        for compose_path in sorted(subdir.glob("*.yml")):
            expected.add(os.path.relpath(compose_path, root_dir))

    return expected


def _validate_overlay_compose_coverage(root_dir: str, overlays: Mapping[str, OverlaySpec]) -> None:
    expected = _expected_overlay_compose_files(root_dir)
    declared = {overlay.compose_file for overlay in overlays.values()}

    missing = sorted(expected - declared)
    if missing:
        raise ValueError(f"Missing overlay definitions for compose files: {missing}")

    unexpected = sorted(declared - expected)
    if unexpected:
        raise ValueError(f"Overlay definitions reference out-of-scope compose files: {unexpected}")


def _validate_model_toml_pairs(root_dir: str) -> None:
    model_root = Path(root_dir) / "compose" / "models"
    if not model_root.is_dir():
        raise ValueError(f"compose/models directory missing at {model_root}")

    for base_yml in sorted(model_root.rglob("base.yml")):
        sibling_toml = base_yml.with_name("model.toml")
        if not sibling_toml.is_file():
            rel = os.path.relpath(sibling_toml, root_dir)
            raise ValueError(f"Missing TOML sibling for model base compose file: {rel}")

    for model_toml in sorted(model_root.rglob("model.toml")):
        sibling_yml = model_toml.with_name("base.yml")
        if not sibling_yml.is_file():
            rel = os.path.relpath(model_toml, root_dir)
            raise ValueError(
                f"Model metadata file requires sibling base.yml in the same directory: {rel}"
            )


def load_env_schema(root_dir: str, *, strict: bool = True) -> EnvSchema:
    root_path = Path(root_dir)
    compose_dir = root_path / "compose"
    if not compose_dir.is_dir():
        raise ValueError(f"compose directory missing at {compose_dir}")

    if strict:
        _validate_model_toml_pairs(root_dir)

    schema_path = compose_dir / "schema.toml"
    if not schema_path.is_file():
        raise ValueError(f"Missing schema TOML: {schema_path}")

    schema_data = _load_toml(schema_path)
    catalog = _parse_catalog(schema_data, schema_path)

    raw_overlays = schema_data.get("overlays", [])
    if not isinstance(raw_overlays, list) or not raw_overlays:
        raise ValueError(f"Invalid schema TOML (missing [[overlays]]) at {schema_path}")

    overlays: dict[str, OverlaySpec] = {}
    seen_compose: dict[str, str] = {}
    for raw_overlay in raw_overlays:
        overlay = _parse_overlay(
            raw_overlay,
            schema_path=schema_path,
            root_dir=root_dir,
        )

        if overlay.overlay_id in overlays:
            raise ValueError(f"Duplicate overlay.id '{overlay.overlay_id}'")
        if overlay.compose_file in seen_compose:
            other_id = seen_compose[overlay.compose_file]
            raise ValueError(
                f"Conflicting overlay definitions for {overlay.compose_file}: "
                f"{other_id} and {overlay.overlay_id}"
            )

        seen_compose[overlay.compose_file] = overlay.overlay_id
        overlays[overlay.overlay_id] = overlay

    overlay_bindings = _parse_schema_bindings(
        schema_data,
        schema_path=schema_path,
        catalog=catalog,
        overlays=overlays,
    )
    overlays = {
        overlay_id: replace(overlay, variables=overlay_bindings.get(overlay_id, ()))
        for overlay_id, overlay in overlays.items()
    }

    model_family_paths = sorted((compose_dir / "models").rglob("model.toml"))
    model_families: dict[str, ModelFamilySpec] = {}
    for model_family_path in model_family_paths:
        family = _parse_model_family(
            _load_toml(model_family_path),
            model_path=model_family_path,
            root_dir=root_dir,
            catalog=catalog,
        )
        if family.family_id in model_families:
            raise ValueError(f"Duplicate model.id '{family.family_id}'")
        model_families[family.family_id] = family

    if strict:
        _validate_overlay_compose_coverage(root_dir, overlays)

    return EnvSchema(
        root_dir=root_dir,
        catalog=catalog,
        overlays=overlays,
        model_families=model_families,
    )


def _term_matches(
    term: str,
    *,
    context: SelectionContext,
    env: Mapping[str, str],
) -> bool:
    if not term:
        return False
    if term == "always":
        return True
    if ":" not in term:
        return False

    left, right = term.split(":", 1)
    value = right.strip()
    if left == "mode":
        return context.mode == value
    if left == "feature":
        return value in context.enabled_features
    if left == "app":
        return value in context.enabled_apps
    if left == "env_nonempty":
        return bool((env.get(value, "") or "").strip())
    if left == "env_empty":
        return not bool((env.get(value, "") or "").strip())
    if left == "env_true":
        return parse_bool(env.get(value, ""))
    if left == "env_false":
        return not parse_bool(env.get(value, ""))
    return False


def condition_matches(
    *,
    all_terms: tuple[str, ...] | list[str],
    any_terms: tuple[str, ...] | list[str],
    context: SelectionContext,
    env: Mapping[str, str],
) -> bool:
    all_list = tuple(item for item in all_terms if item)
    any_list = tuple(item for item in any_terms if item)

    if all_list:
        for term in all_list:
            if not _term_matches(term, context=context, env=env):
                return False

    if any_list:
        if not any(_term_matches(term, context=context, env=env) for term in any_list):
            return False

    return True


def ref_is_applicable(ref: VariableRef, *, context: SelectionContext, env: Mapping[str, str]) -> bool:
    return condition_matches(
        all_terms=ref.applicable_when_all,
        any_terms=ref.applicable_when_any,
        context=context,
        env=env,
    )


def ref_is_required(ref: VariableRef, *, context: SelectionContext, env: Mapping[str, str]) -> bool:
    if not ref.required_when_all and not ref.required_when_any:
        return False
    return condition_matches(
        all_terms=ref.required_when_all,
        any_terms=ref.required_when_any,
        context=context,
        env=env,
    )


def selected_overlays(
    schema: EnvSchema,
    *,
    context: SelectionContext,
    env: Mapping[str, str],
) -> list[OverlaySpec]:
    selected_ids: set[str] = set()

    for overlay in schema.by_kind("base"):
        selected_ids.add(overlay.overlay_id)

    mode_overlay = schema.mode_overlay(context.mode)
    if mode_overlay:
        selected_ids.add(mode_overlay.overlay_id)

    for feature_name in context.enabled_features:
        feature_overlay = schema.feature_overlay(feature_name)
        if feature_overlay:
            selected_ids.add(feature_overlay.overlay_id)

    for app_name in context.enabled_apps:
        app_overlay = schema.app_overlay(app_name)
        if app_overlay:
            selected_ids.add(app_overlay.overlay_id)

    # Auto-enabled overlays such as dmz_egress.
    for overlay in schema.overlays.values():
        if overlay.overlay_id in selected_ids:
            continue
        if not overlay.auto_enable_when_any:
            continue
        if condition_matches(
            all_terms=(),
            any_terms=overlay.auto_enable_when_any,
            context=context,
            env=env,
        ):
            selected_ids.add(overlay.overlay_id)

    selected = [schema.overlays[overlay_id] for overlay_id in selected_ids]
    return sorted(selected, key=lambda item: (item.order, item.kind, item.name))


def merged_variable_refs(overlays: list[OverlaySpec]) -> list[VariableRef]:
    by_var: dict[str, VariableRef] = {}
    for overlay in overlays:
        for ref in overlay.variables:
            current = by_var.get(ref.var_id)
            if current is None or ref.prompt_order < current.prompt_order:
                by_var[ref.var_id] = ref
    return sorted(by_var.values(), key=lambda item: (item.prompt_order, item.var_id))


def resolve_effective_variable(schema: EnvSchema, ref: VariableRef) -> EffectiveVariable:
    spec = schema.catalog[ref.var_id]
    description = ref.description or spec.description
    examples = ref.examples if ref.examples else spec.examples
    return EffectiveVariable(
        ref=ref,
        spec=spec,
        description=description,
        examples=examples,
    )
def resolve_model_family_from_id(
    schema: EnvSchema,
    family_id: str,
) -> ModelFamilySpec | None:
    return schema.model_families.get((family_id or "").strip())
