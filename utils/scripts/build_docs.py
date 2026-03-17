#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.stack.schema import (
    SelectionContext,
    load_env_schema,
    ref_is_applicable,
    ref_is_required,
    resolve_effective_variable,
)


def _example_value(default_value: str, examples: tuple[str, ...], secret: bool) -> str:
    if examples:
        return f"\"{examples[0]}\""
    if default_value:
        return f"\"{default_value}\""
    if secret:
        return "\"<secret>\""
    return "\"<value>\""


def _classify_variables(schema, overlay):
    if overlay.kind == "mode":
        context = SelectionContext(mode=overlay.name, enabled_features=frozenset(), enabled_apps=frozenset())
    elif overlay.kind == "feature":
        mode = overlay.availability_modes[0] if overlay.availability_modes else "chatbot_provider"
        context = SelectionContext(mode=mode, enabled_features=frozenset({overlay.name}), enabled_apps=frozenset())
    else:
        mode = overlay.availability_modes[0] if overlay.availability_modes else "batch_client"
        context = SelectionContext(mode=mode, enabled_features=frozenset(), enabled_apps=frozenset({overlay.name}))

    env_view = {}
    required = []
    optional = []
    for ref in overlay.variables:
        if not ref_is_applicable(ref, context=context, env=env_view):
            continue
        resolved = resolve_effective_variable(schema, ref)
        if ref_is_required(ref, context=context, env=env_view):
            required.append((ref, resolved))
        else:
            optional.append((ref, resolved))
    return required, optional


def _render_export_block(lines: list[str], pairs: list[tuple[str, str]]) -> None:
    if not pairs:
        return
    lines.append("```bash")
    for key, value in pairs:
        lines.append(f"export {key}={value}")
    lines.append("```")


def _render_variable_list(lines: list[str], title: str, pairs: list[tuple]) -> None:
    if not pairs:
        return
    lines.append(f"## {title}")
    lines.append("")
    for _ref, resolved in pairs:
        spec = resolved.spec
        details = []
        if spec.secret:
            details.append("secret")
        if spec.default:
            details.append(f"default: `{spec.default}`")
        if resolved.examples:
            details.append(f"example: `{resolved.examples[0]}`")
        suffix = f" ({', '.join(details)})" if details else ""
        lines.append(f"- `{spec.var_id}`{suffix}: {resolved.description}")
    lines.append("")


def _render_links(lines: list[str], title: str, overlays: list) -> None:
    if not overlays:
        return
    lines.append(f"## {title}")
    lines.append("")
    for overlay in overlays:
        lines.append(f"- [{overlay.title}](../{overlay.doc_path.split('/', 1)[1]})")
    lines.append("")


def _render_model_families_section(schema) -> str:
    lines: list[str] = []
    lines.append("## Model Catalog")
    lines.append("")
    lines.append("This section is generated from `compose/models/**/model.toml` metadata.")
    lines.append("")

    context = SelectionContext(
        mode="chatbot_provider",
        enabled_features=frozenset({"embedding_backend", "stt_backend"}),
        enabled_apps=frozenset(),
    )
    env_view: dict[str, str] = {}
    role_titles = {"llm": "LLM", "embedding": "Embedding", "stt": "STT"}
    for role in ("llm", "embedding", "stt"):
        families = schema.model_families_by_role(role, wizard_only=True)
        if not families:
            continue
        lines.append(f"### {role_titles[role]} Families")
        lines.append("")
        for family in families:
            lines.append(f"#### {family.title}")
            lines.append("")
            lines.append(family.summary)
            lines.append("")
            lines.append(f"- Model family ID: `{family.family_id}`")
            lines.append(f"- Base template: `{family.base_compose_file}`")
            lines.append(f"- Accelerator: `{family.accelerator}`")
            lines.append(
                f"- GPU architecture presets: `{', '.join(sorted(family.architectures.keys()))}`"
            )
            if family.runtime.default_vllm_openai_image:
                lines.append(
                    f"- Default worker image: `{family.runtime.default_vllm_openai_image}`"
                )
            lines.append("")

            required_vars = []
            optional_vars = []
            for ref in family.variables:
                if not ref_is_applicable(ref, context=context, env=env_view):
                    continue
                resolved = resolve_effective_variable(schema, ref)
                if ref_is_required(ref, context=context, env=env_view):
                    required_vars.append((ref, resolved))
                else:
                    optional_vars.append((ref, resolved))

            if required_vars:
                lines.append("Required model variables:")
                lines.append("")
                for _ref, resolved in required_vars:
                    details = []
                    if resolved.examples:
                        details.append(f"example: `{resolved.examples[0]}`")
                    if resolved.spec.default:
                        details.append(f"default: `{resolved.spec.default}`")
                    suffix = f" ({', '.join(details)})" if details else ""
                    lines.append(f"- `{resolved.spec.var_id}`{suffix}: {resolved.description}")
                lines.append("")

            if optional_vars:
                lines.append("Optional model variables:")
                lines.append("")
                for _ref, resolved in optional_vars:
                    details = []
                    if resolved.examples:
                        details.append(f"example: `{resolved.examples[0]}`")
                    if resolved.spec.default:
                        details.append(f"default: `{resolved.spec.default}`")
                    suffix = f" ({', '.join(details)})" if details else ""
                    lines.append(f"- `{resolved.spec.var_id}`{suffix}: {resolved.description}")
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _write_generated_section(path: Path, *, marker_name: str, section_text: str) -> None:
    start_marker = f"<!-- {marker_name}_START -->"
    end_marker = f"<!-- {marker_name}_END -->"
    content = path.read_text(encoding="utf-8")
    start_idx = content.find(start_marker)
    end_idx = content.find(end_marker)
    generated = f"{start_marker}\n{section_text}{end_marker}"
    if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
        if not content.endswith("\n"):
            content += "\n"
        content = f"{content}\n{generated}\n"
    else:
        end_idx += len(end_marker)
        content = f"{content[:start_idx]}{generated}{content[end_idx:]}"
        if not content.endswith("\n"):
            content += "\n"
    path.write_text(content, encoding="utf-8")


def _render_overlay_doc(schema, overlay, output_path: Path) -> None:
    required_vars, optional_vars = _classify_variables(schema, overlay)
    lines: list[str] = []

    lines.append("<!-- GENERATED FILE: DO NOT EDIT. Run utils/scripts/build_docs.py -->")
    lines.append(f"# {overlay.title}")
    lines.append("")
    lines.append(overlay.summary)
    lines.append("")

    lines.append("Availability:")
    lines.append("")
    for mode_name in overlay.availability_modes:
        lines.append(f"- {mode_name.replace('_', ' ')}")
    lines.append("")

    exports: list[tuple[str, str]] = []
    if overlay.toggle_var:
        exports.append((overlay.toggle_var, "\"true\""))
    if overlay.kind == "mode":
        if overlay.name == "batch_client":
            exports.append(("BATCH_CLIENT_MODE_ON", "\"true\""))
        elif overlay.name == "chatbot_provider":
            exports.append(("BATCH_CLIENT_MODE_ON", "\"false\""))
    for _ref, resolved in required_vars:
        spec = resolved.spec
        if spec.var_id == overlay.toggle_var:
            continue
        value = _example_value(spec.default or "", resolved.examples, spec.secret)
        exports.append((spec.var_id, value))
    deduped_exports: list[tuple[str, str]] = []
    seen_export_keys: set[str] = set()
    for key, value in exports:
        if key in seen_export_keys:
            continue
        seen_export_keys.add(key)
        deduped_exports.append((key, value))
    exports = deduped_exports

    if exports:
        lines.append("## Example Configuration")
        lines.append("")
        _render_export_block(lines, exports)
        lines.append("")

    if overlay.use_when:
        lines.append("## Use When")
        lines.append("")
        for item in overlay.use_when:
            lines.append(f"- {item}")
        lines.append("")

    if overlay.behavior:
        lines.append("## Behavior")
        lines.append("")
        for item in overlay.behavior:
            lines.append(f"- {item}")
        lines.append("")

    if overlay.verify:
        lines.append("## Verify")
        lines.append("")
        for item in overlay.verify:
            lines.append(f"- {item}")
        lines.append("")

    if overlay.access:
        lines.append("## Access")
        lines.append("")
        for item in overlay.access:
            lines.append(f"- {item}")
        lines.append("")

    _render_variable_list(lines, "Required Variables", required_vars)
    _render_variable_list(lines, "Optional Variables", optional_vars)

    if overlay.kind == "mode":
        by_name = {item.name: item for item in schema.overlays.values()}
        feature_links = [by_name[name] for name in overlay.compatible_features if name in by_name]
        app_links = [by_name[name] for name in overlay.compatible_apps if name in by_name]
        _render_links(lines, "Compatible Features", feature_links)
        _render_links(lines, "Compatible Apps", app_links)

    lines.append("Related compose overlay:")
    lines.append("")
    lines.append(f"- `{overlay.compose_file}`")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def generate_docs(root_dir: Path) -> None:
    schema = load_env_schema(str(root_dir), strict=True)
    for kind in ("mode", "feature", "app"):
        for overlay in schema.by_kind(kind):
            if not overlay.doc_path:
                continue
            output_path = root_dir / overlay.doc_path
            _render_overlay_doc(schema, overlay, output_path)
    models_readme = root_dir / "compose" / "models" / "README.md"
    _write_generated_section(
        models_readme,
        marker_name="GENERATED_MODELS",
        section_text=_render_model_families_section(schema),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate docs/modes|features|apps from TOML env schema metadata.")
    parser.add_argument(
        "--root-dir",
        default=str(REPO_ROOT),
        help="Repository root (default: repo root).",
    )
    args = parser.parse_args()
    generate_docs(Path(args.root_dir).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
