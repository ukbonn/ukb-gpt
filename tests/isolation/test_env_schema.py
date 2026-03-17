from pathlib import Path

import pytest

from utils.stack import schema as env_schema


pytestmark = [pytest.mark.isolation, pytest.mark.chatbot_provider, pytest.mark.batch_client]

ROOT = Path(__file__).resolve().parents[2]


def test_overlay_compose_files_are_covered_by_schema():
    schema = env_schema.load_env_schema(str(ROOT), strict=True)
    expected = {"compose/base.yml"}
    for subdir in ("modes", "features", "apps"):
        expected.update(
            str(path.relative_to(ROOT))
            for path in (ROOT / "compose" / subdir).glob("*.yml")
        )
    assert {overlay.compose_file for overlay in schema.overlays.values()} == expected


def test_binding_without_variable_definition_fails_strict_parse(tmp_path):
    dst_root = tmp_path / "repo"
    (dst_root / "compose" / "features").mkdir(parents=True)
    (dst_root / "compose" / "modes").mkdir(parents=True)
    (dst_root / "compose" / "apps").mkdir(parents=True)
    # Copy full compose tree to satisfy strict checks.
    for path in (ROOT / "compose").rglob("*"):
        rel = path.relative_to(ROOT / "compose")
        target = dst_root / "compose" / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

    schema_path = dst_root / "compose" / "schema.toml"
    broken = schema_path.read_text(encoding="utf-8")
    broken += """

[[vars.DOES_NOT_EXIST.bindings]]
overlay = "feature.metrics"
prompt_order = 9999
prompt = true
"""
    schema_path.write_text(broken, encoding="utf-8")

    with pytest.raises(ValueError, match="missing type"):
        env_schema.load_env_schema(str(dst_root), strict=True)


def test_unknown_binding_overlay_fails_strict_parse(tmp_path):
    dst_root = tmp_path / "repo"
    for path in (ROOT / "compose").rglob("*"):
        rel = path.relative_to(ROOT / "compose")
        target = dst_root / "compose" / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

    schema_path = dst_root / "compose" / "schema.toml"
    broken = schema_path.read_text(encoding="utf-8")
    broken += """

[[vars.SERVER_NAME.bindings]]
overlay = "feature.does_not_exist"
prompt_order = 9999
prompt = true
"""
    schema_path.write_text(broken, encoding="utf-8")

    with pytest.raises(ValueError, match="unknown overlay"):
        env_schema.load_env_schema(str(dst_root), strict=True)


def test_missing_model_toml_fails_strict_parse(tmp_path):
    dst_root = tmp_path / "repo"
    for path in (ROOT / "compose").rglob("*"):
        rel = path.relative_to(ROOT / "compose")
        target = dst_root / "compose" / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

    target_toml = dst_root / "compose" / "models" / "llm" / "qwen--qwen3-1.7b" / "model.toml"
    target_toml.unlink()

    with pytest.raises(ValueError, match="Missing TOML sibling for model base compose file"):
        env_schema.load_env_schema(str(dst_root), strict=True)


def test_unknown_catalog_reference_in_model_family_fails_strict_parse(tmp_path):
    dst_root = tmp_path / "repo"
    for path in (ROOT / "compose").rglob("*"):
        rel = path.relative_to(ROOT / "compose")
        target = dst_root / "compose" / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

    model_toml = dst_root / "compose" / "models" / "llm" / "qwen--qwen3-1.7b" / "model.toml"
    broken = model_toml.read_text(encoding="utf-8")
    broken += "\n[[variables]]\nid = \"DOES_NOT_EXIST\"\n"
    model_toml.write_text(broken, encoding="utf-8")

    with pytest.raises(ValueError, match="references unknown catalog variable"):
        env_schema.load_env_schema(str(dst_root), strict=True)


def test_invalid_model_architecture_fails_strict_parse(tmp_path):
    dst_root = tmp_path / "repo"
    for path in (ROOT / "compose").rglob("*"):
        rel = path.relative_to(ROOT / "compose")
        target = dst_root / "compose" / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

    model_toml = dst_root / "compose" / "models" / "llm" / "qwen--qwen3-1.7b" / "model.toml"
    broken = model_toml.read_text(encoding="utf-8")
    broken += "\n[architectures.invalid_gpu]\n"
    model_toml.write_text(broken, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid architecture preset"):
        env_schema.load_env_schema(str(dst_root), strict=True)


def test_missing_model_runtime_table_fails_strict_parse(tmp_path):
    dst_root = tmp_path / "repo"
    for path in (ROOT / "compose").rglob("*"):
        rel = path.relative_to(ROOT / "compose")
        target = dst_root / "compose" / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

    model_toml = dst_root / "compose" / "models" / "llm" / "qwen--qwen3-1.7b" / "model.toml"
    broken = model_toml.read_text(encoding="utf-8").replace("\n[runtime]\n", "\n[runtime_removed]\n", 1)
    model_toml.write_text(broken, encoding="utf-8")

    with pytest.raises(ValueError, match="must define a \\[runtime\\] table"):
        env_schema.load_env_schema(str(dst_root), strict=True)


def test_duplicate_model_family_id_fails_strict_parse(tmp_path):
    dst_root = tmp_path / "repo"
    for path in (ROOT / "compose").rglob("*"):
        rel = path.relative_to(ROOT / "compose")
        target = dst_root / "compose" / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())

    target_model_toml = dst_root / "compose" / "models" / "embedding" / "alibaba-nlp--gte-qwen2-1.5b-instruct" / "model.toml"
    source_id = 'id = "model.llm.qwen_qwen3_1_7b"'
    target_text = target_model_toml.read_text(encoding="utf-8")
    target_text = target_text.replace(
        'id = "model.embedding.alibaba_nlp_gte_qwen2_1_5b"',
        source_id,
    )
    target_model_toml.write_text(target_text, encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate model.id"):
        env_schema.load_env_schema(str(dst_root), strict=True)
