import json
import uuid
from pathlib import Path

from tests.helpers.commands import assert_ok, docker_exec, docker_exec_python, retry_until
from tests.helpers.docker import compose, compose_flags_with_generated_models
from tests.integration.common import CHATBOT_PROVIDER_MARKS
from tests.helpers.chat_purger_db import (
    build_probe_counts_script,
    build_seed_probe_rows_script,
)

pytestmark = CHATBOT_PROVIDER_MARKS

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_PURGER_COMPOSE_FILES = (
    REPO_ROOT / "compose/base.yml",
    REPO_ROOT / "compose/modes/frontend.provider.yml",
    REPO_ROOT / "compose/features/dmz_egress.yml",
    REPO_ROOT / "compose/features/ldap.yml",
    REPO_ROOT / "compose/features/metrics.yml",
    REPO_ROOT / "compose/features/chat_purger.yml",
)


def _chat_purger_compose_flags(env: dict[str, str]) -> list[str]:
    return compose_flags_with_generated_models(REPO_ROOT, *CHAT_PURGER_COMPOSE_FILES, env=env)


def _container_python(container_name: str, script: str) -> str:
    res = docker_exec_python(container_name, script)
    assert_ok(res, f"Failed to run python inside {container_name}")
    return (res.stdout or "").strip()


def _wait_for_frontend_ready(container_name: str = "ukbgpt_frontend") -> None:
    def _ready() -> bool:
        return docker_exec(
            container_name,
            ["curl", "-fsS", "-m", "3", "http://127.0.0.1:8080/health"],
        ).code == 0

    assert retry_until(_ready, attempts=30, delay_seconds=2), (
        f"{container_name} did not become ready on /health before chat_purger seeding"
    )


def _seed_probe_rows(old_id: str, new_id: str, container_name: str = "ukbgpt_frontend") -> None:
    _container_python(container_name, build_seed_probe_rows_script(old_id, new_id))


def _probe_counts(old_id: str, new_id: str, container_name: str = "ukbgpt_frontend") -> dict:
    output = _container_python(container_name, build_probe_counts_script(old_id, new_id))
    return json.loads(output)


def test_chat_purger_deletes_old_chats_only(chatbot_provider_stack):
    env = dict(chatbot_provider_stack.env)
    env.update(
        {
            "ENABLE_CHAT_PURGER": "true",
            "CHAT_HISTORY_RETENTION_DAYS": "1",
            "CHAT_HISTORY_PURGE_INTERVAL_SECONDS": "2",
        }
    )

    old_id = f"retention-old-{uuid.uuid4().hex}"
    new_id = f"retention-new-{uuid.uuid4().hex}"

    try:
        _wait_for_frontend_ready()
        # Seed rows before starting chat_purger to avoid a startup race where
        # the first purge cycle deletes old rows before baseline assertions.
        _seed_probe_rows(old_id, new_id)
        initial = _probe_counts(old_id, new_id)
        assert initial["old_chat"] == 1, f"Expected seeded old chat. Probe: {initial}"
        assert initial["new_chat"] == 1, f"Expected seeded new chat. Probe: {initial}"

        up = compose(
            _chat_purger_compose_flags(env),
            ["up", "-d", "--build", "--no-deps", "chat_purger"],
            env=env,
        )
        assert_ok(up, "Failed to start chat_purger in chatbot-provider-mode test stack")

        def _purged() -> bool:
            probe = _probe_counts(old_id, new_id)
            return (
                probe["old_chat"] == 0
                and probe["new_chat"] == 1
                and probe["old_tag"] == 0
            )

        assert retry_until(_purged, attempts=30, delay_seconds=2), (
            "chat_purger did not delete old chat/tag while preserving recent chat "
            f"(old_id={old_id}, new_id={new_id})"
        )
    finally:
        rm = compose(_chat_purger_compose_flags(env), ["rm", "-sf", "chat_purger"], env=env)
        assert_ok(rm, "Failed to remove chat_purger test container")
