import builtins
import importlib.util
from pathlib import Path
import shutil

import pytest


pytestmark = [pytest.mark.isolation, pytest.mark.chatbot_provider, pytest.mark.batch_client]

ROOT = Path(__file__).resolve().parents[2]
WIZARD_PATH = ROOT / "utils" / "scripts" / "configure_env.py"


def _load_wizard_module():
    spec = importlib.util.spec_from_file_location("env_wizard_module", WIZARD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def _prepare_schema_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "compose", root / "compose")
    return root


def test_wizard_writes_env_new_and_omits_secrets(monkeypatch, tmp_path, capsys):
    wizard = _load_wizard_module()
    schema_root = _prepare_schema_root(tmp_path)
    (schema_root / ".env").write_text("KEEP=1\n", encoding="utf-8")
    prompts_seen: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts_seen.append(prompt)
        if prompt.strip() == "Selection:":
            return "1"
        if "Mode [default 1]" in prompt:
            return "1"
        if "Enable feature: Metrics" in prompt:
            return "n"
        if "Enable feature: LDAP Integration" in prompt:
            return "n"
        if "Enable feature: Chat Purger" in prompt:
            return "n"
        if "Enable app: Dictation App" in prompt:
            return "n"
        if prompt.strip() == "Profile:":
            return "2"
        if "GPU architecture [default 1]" in prompt:
            return ""
        if "Set GPU groups" in prompt:
            return "0"
        if "Set SSL_CERT_PATH" in prompt:
            return "/tmp/fullchain.pem"
        if "Set OPENWEBUI_DATA_DIR" in prompt:
            return "/tmp/openwebui-data"
        return ""

    monkeypatch.setattr(builtins, "input", fake_input)

    code = wizard.run_wizard(schema_root)
    assert code == 0

    env_file = schema_root / ".env"
    assert env_file.is_file()
    text = env_file.read_text(encoding="utf-8")
    assert "SSL_CERT_PATH" in text
    assert "OPENWEBUI_DATA_DIR" in text
    assert "CERTIFICATE_KEY" not in text
    assert "WEBUI_SECRET_KEY" not in text

    out = capsys.readouterr().out
    assert "read -sr CERTIFICATE_KEY" in out
    assert "export CERTIFICATE_KEY=" in out
    assert "export CERTIFICATE_KEY_FILE=" in out
    assert "export WEBUI_SECRET_KEY_FILE=" in out
    assert not any("Set ROOT_CA_PATH" in prompt for prompt in prompts_seen)
    assert not any("Set OPENWEBUI_RUNTIME_UID" in prompt for prompt in prompts_seen)
    assert not any("Set OPENWEBUI_RUNTIME_GID" in prompt for prompt in prompts_seen)
    model_deployment_line = next(
        line for line in text.splitlines() if line.startswith("MODEL_DEPLOYMENT_CONFIG=")
    )
    generated_path = model_deployment_line.split('"')[1]
    assert generated_path.startswith("compose/generated/deployments/llm/")
    assert generated_path.endswith("/deployment-01.toml")
    assert (schema_root / generated_path).is_file()
