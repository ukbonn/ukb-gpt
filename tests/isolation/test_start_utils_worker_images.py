import pytest

from utils.models import deployment as model_deployment
from utils.stack import deployments as deployment_service
from utils.stack import startup as start_utils

pytestmark = [pytest.mark.isolation, pytest.mark.chatbot_provider, pytest.mark.batch_client]


_IMAGE_ENV_VARS = [
    "VLLM_OPENAI_IMAGE",
    "VLLM_OPENAI_IMAGE_LLM",
    "VLLM_OPENAI_IMAGE_EMBEDDING",
    "VLLM_OPENAI_IMAGE_STT",
]


def _clear_image_env(monkeypatch) -> None:
    for var_name in _IMAGE_ENV_VARS:
        monkeypatch.delenv(var_name, raising=False)


def _patch_tls_material_validators(monkeypatch) -> None:
    monkeypatch.setattr(
        start_utils,
        "_validate_key_material",
        lambda _value, *, label: True,
    )
    monkeypatch.setattr(
        start_utils,
        "_validate_pem_certificate_file",
        lambda _path, *, label: True,
    )


def test_validate_model_specific_requirements_gpt_oss_requires_encodings(monkeypatch, capsys):
    monkeypatch.setenv("BATCH_CLIENT_MODE_ON", "false")
    monkeypatch.delenv("GPT_OSS_ENCODINGS_PATH", raising=False)
    monkeypatch.setattr(
        model_deployment,
        "inspect_local_nvidia_gpus",
        lambda: {
            0: model_deployment.LocalGpuInfo(index=0, name="A100", gpu_architecture="nvidia_ampere"),
            1: model_deployment.LocalGpuInfo(index=1, name="A100", gpu_architecture="nvidia_ampere"),
            2: model_deployment.LocalGpuInfo(index=2, name="A100", gpu_architecture="nvidia_ampere"),
            3: model_deployment.LocalGpuInfo(index=3, name="A100", gpu_architecture="nvidia_ampere"),
        },
    )

    resolved = deployment_service.resolve_selected_model_deployments(
        llm_deployment_config="tests/model_deployments/gpt-oss-2x2.toml",
        embedding_deployment_config="",
        stt_deployment_config="",
        runtime_mode="chatbot_provider",
        root_dir=start_utils.ROOT_DIR,
    )
    with pytest.raises(SystemExit) as exc_info:
        deployment_service.validate_model_specific_requirements(
            resolved,
            root_dir=start_utils.ROOT_DIR,
        )

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "GPT_OSS_ENCODINGS_PATH is required" in output


def test_validate_environment_rejects_direct_secret_and_file_var_together(monkeypatch, tmp_path, capsys):
    openwebui_data_dir = tmp_path / "openwebui-data"
    cert_file = tmp_path / "server.key"
    cert_file.write_text("file-cert-key", encoding="utf-8")

    monkeypatch.setenv("BATCH_CLIENT_MODE_ON", "false")
    monkeypatch.setenv("CERTIFICATE_KEY", "direct-cert-key")
    monkeypatch.setenv("CERTIFICATE_KEY_FILE", str(cert_file))
    monkeypatch.setenv("WEBUI_SECRET_KEY", "dummy-secret")
    monkeypatch.setenv("SSL_CERT_PATH", "/fake/server.crt")
    monkeypatch.setenv("OPENWEBUI_DATA_DIR", str(openwebui_data_dir))
    monkeypatch.delenv("OPENWEBUI_RUNTIME_UID", raising=False)
    monkeypatch.delenv("OPENWEBUI_RUNTIME_GID", raising=False)
    monkeypatch.setattr(start_utils.os.path, "isfile", lambda _path: True)
    _patch_tls_material_validators(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        start_utils.validate_environment()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "both CERTIFICATE_KEY and CERTIFICATE_KEY_FILE are set" in output


def test_validate_environment_requires_openwebui_data_dir(monkeypatch, capsys):
    monkeypatch.setenv("BATCH_CLIENT_MODE_ON", "false")
    monkeypatch.setenv("CERTIFICATE_KEY", "dummy")
    monkeypatch.setenv("SSL_CERT_PATH", "/fake/server.crt")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "dummy-secret")
    monkeypatch.delenv("OPENWEBUI_DATA_DIR", raising=False)
    monkeypatch.delenv("OPENWEBUI_RUNTIME_UID", raising=False)
    monkeypatch.delenv("OPENWEBUI_RUNTIME_GID", raising=False)
    monkeypatch.setattr(start_utils.os.path, "isfile", lambda _path: True)

    with pytest.raises(SystemExit) as exc_info:
        start_utils.validate_environment()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "Warning: OPENWEBUI_DATA_DIR is unset" in output
    assert "OPENWEBUI_DATA_DIR is required in chatbot provider mode" in output


def test_validate_environment_rejects_repo_local_openwebui_data_dir(monkeypatch, tmp_path, capsys):
    schema = start_utils._load_runtime_env_schema()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo_local_dir = repo_root / "state" / "openwebui-data"

    monkeypatch.setenv("BATCH_CLIENT_MODE_ON", "false")
    monkeypatch.setenv("CERTIFICATE_KEY", "dummy")
    monkeypatch.setenv("SSL_CERT_PATH", "/fake/server.crt")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "dummy-secret")
    monkeypatch.setenv("OPENWEBUI_DATA_DIR", str(repo_local_dir))
    monkeypatch.delenv("OPENWEBUI_RUNTIME_UID", raising=False)
    monkeypatch.delenv("OPENWEBUI_RUNTIME_GID", raising=False)
    monkeypatch.setattr(start_utils, "ROOT_DIR", str(repo_root))
    monkeypatch.setattr(start_utils.os.path, "isfile", lambda _path: True)
    _patch_tls_material_validators(monkeypatch)

    selection = start_utils.SchemaRuntimeSelection(
        schema=schema,
        context=start_utils.SelectionContext(
            mode="chatbot_provider",
            enabled_features=frozenset(),
            enabled_apps=frozenset(),
        ),
        overlays=[],
        enabled_features={},
        enabled_apps={},
    )

    with pytest.raises(SystemExit) as exc_info:
        start_utils.validate_environment(selection=selection)

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "OPENWEBUI_DATA_DIR must not point inside the repository tree" in output
    assert str(repo_local_dir) in output
    assert not repo_local_dir.exists()


def test_validate_environment_rejects_non_rfc1918_llm_target_ip(monkeypatch, capsys):
    monkeypatch.setenv("BATCH_CLIENT_MODE_ON", "true")
    monkeypatch.setenv("ENABLE_DATASET_STRUCTURING_APP", "false")
    monkeypatch.setenv("ROOT_CA_PATH", "/fake/root-ca.crt")
    monkeypatch.setenv("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS", "https://llm.internal:8443")
    monkeypatch.setenv("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_IP", "8.8.8.8")
    monkeypatch.setenv("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS", "")
    monkeypatch.setenv("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_IP", "")
    monkeypatch.setenv("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_SNI", "")
    monkeypatch.delenv("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_SNI", raising=False)
    monkeypatch.delenv("ENABLE_API_EGRESS", raising=False)
    monkeypatch.setattr(start_utils.os.path, "isfile", lambda _path: True)

    with pytest.raises(SystemExit) as exc_info:
        start_utils.validate_environment()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "RFC1918 private IPv4 address" in output
    assert "BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_IP" in output


def test_validate_environment_rejects_ldap_without_root_ca(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BATCH_CLIENT_MODE_ON", "false")
    monkeypatch.setenv("ENABLE_LDAP", "true")
    monkeypatch.setenv("LDAP_TARGET_IP", "10.23.45.67")
    monkeypatch.setenv("CERTIFICATE_KEY", "dummy")
    monkeypatch.setenv("SSL_CERT_PATH", "/fake/server.crt")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "dummy-secret")
    monkeypatch.setenv("OPENWEBUI_DATA_DIR", str(tmp_path / "openwebui-data"))
    monkeypatch.delenv("ROOT_CA_PATH", raising=False)
    monkeypatch.setattr(start_utils.os.path, "isfile", lambda _path: True)

    with pytest.raises(SystemExit) as exc_info:
        start_utils.validate_environment()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "ROOT_CA_PATH must be set when ENABLE_LDAP=true" in output


def test_validate_environment_rejects_ldap_without_target_sni(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BATCH_CLIENT_MODE_ON", "false")
    monkeypatch.setenv("ENABLE_LDAP", "true")
    monkeypatch.setenv("LDAP_TARGET_IP", "10.23.45.67")
    monkeypatch.setenv("ROOT_CA_PATH", "/fake/root-ca.crt")
    monkeypatch.setenv("CERTIFICATE_KEY", "dummy")
    monkeypatch.setenv("SSL_CERT_PATH", "/fake/server.crt")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "dummy-secret")
    monkeypatch.setenv("OPENWEBUI_DATA_DIR", str(tmp_path / "openwebui-data"))
    monkeypatch.delenv("LDAP_TARGET_SNI", raising=False)
    monkeypatch.setattr(start_utils.os.path, "isfile", lambda _path: True)

    with pytest.raises(SystemExit) as exc_info:
        start_utils.validate_environment()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "LDAP_TARGET_SNI is missing" in output


def test_validate_environment_rejects_non_rfc1918_ldap_target_ip(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BATCH_CLIENT_MODE_ON", "false")
    monkeypatch.setenv("ENABLE_LDAP", "true")
    monkeypatch.setenv("LDAP_TARGET_IP", "8.8.8.8")
    monkeypatch.setenv("ROOT_CA_PATH", "/fake/root-ca.crt")
    monkeypatch.setenv("CERTIFICATE_KEY", "dummy")
    monkeypatch.setenv("SSL_CERT_PATH", "/fake/server.crt")
    monkeypatch.setenv("WEBUI_SECRET_KEY", "dummy-secret")
    monkeypatch.setenv("OPENWEBUI_DATA_DIR", str(tmp_path / "openwebui-data"))
    monkeypatch.setattr(start_utils.os.path, "isfile", lambda _path: True)

    with pytest.raises(SystemExit) as exc_info:
        start_utils.validate_environment()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "RFC1918 private IPv4 address" in output
    assert "LDAP_TARGET_IP" in output


def _set_batch_api_egress_env(monkeypatch) -> None:
    monkeypatch.setenv("BATCH_CLIENT_MODE_ON", "true")
    monkeypatch.setenv("ENABLE_DATASET_STRUCTURING_APP", "false")
    monkeypatch.setenv("ENABLE_COHORT_FEASIBILITY_APP", "false")
    monkeypatch.setenv("ENABLE_DICTATION_APP", "false")
    monkeypatch.setenv("ENABLE_LDAP", "false")
    monkeypatch.setenv("ENABLE_EMBEDDING_BACKEND", "false")
    monkeypatch.setenv("ENABLE_STT_BACKEND", "false")
    monkeypatch.delenv("EMBEDDING_MODEL_DEPLOYMENT_CONFIG", raising=False)
    monkeypatch.delenv("STT_MODEL_DEPLOYMENT_CONFIG", raising=False)


def test_validate_environment_api_egress_toggle_true_requires_address(monkeypatch, capsys):
    _set_batch_api_egress_env(monkeypatch)
    monkeypatch.setenv("ENABLE_API_EGRESS", "true")
    monkeypatch.setenv("ROOT_CA_PATH", "/fake/root-ca.crt")
    monkeypatch.setenv("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS", "")
    monkeypatch.setenv("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS", "")
    monkeypatch.setattr(start_utils.os.path, "isfile", lambda _path: True)

    with pytest.raises(SystemExit) as exc_info:
        start_utils.validate_environment()

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "ENABLE_API_EGRESS=true requires at least one additional upstream address" in output
