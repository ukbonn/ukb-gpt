import os
import stat
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading

import pytest

from tests.helpers.commands import run

pytestmark = [pytest.mark.isolation, pytest.mark.chatbot_provider, pytest.mark.batch_client]

ROOT = Path(__file__).resolve().parents[2]
CHECK_EGRESS = ROOT / "security_helpers" / "check_egress.sh"
HEALTHCHECK = ROOT / "security_helpers" / "healthcheck.sh"
ACTIVE_MONITOR = ROOT / "security_helpers" / "active_isolation_monitoring_entrypoint.sh"
INSTALL_HELPERS = ROOT / "security_helpers" / "install_security_helpers.sh"
NGINX_DEBUG_DUMP = ROOT / "security_helpers" / "nginx_debug_dump.sh"
INSTALL_COPY_SCRIPTS = (
    "active_isolation_monitoring_entrypoint.sh",
    "healthcheck.sh",
    "check_egress.sh",
    "nginx_debug_dump.sh",
)


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, format, *args):  # noqa: A003
        return


def _start_http_server() -> tuple[HTTPServer, threading.Thread]:
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _copy_monitor_entrypoint(tmp_path: Path, fake_check: Path) -> Path:
    entrypoint_copy = tmp_path / "active_isolation_monitoring_entrypoint.sh"
    entrypoint_copy.write_text(
        ACTIVE_MONITOR.read_text(encoding="utf-8").replace(
            "/usr/local/bin/check_egress.sh", str(fake_check)
        ),
        encoding="utf-8",
    )
    entrypoint_copy.chmod(0o755)
    return entrypoint_copy


def _copy_install_script_tree(tmp_path: Path) -> Path:
    script_dir = tmp_path / "security_helpers"
    script_dir.mkdir()

    install_copy = script_dir / "install_security_helpers.sh"
    install_copy.write_text(
        INSTALL_HELPERS.read_text(encoding="utf-8")
        .replace(
            "TARGET_BIN=$(command -v ping 2>/dev/null || true)",
            'TARGET_BIN=$(command -v "${PING_BINARY_NAME:-ping}" 2>/dev/null || true)',
        )
        .replace('"/usr/local/bin/$script"', '"${INSTALL_BIN_DIR:-/usr/local/bin}/$script"')
        .replace("/tmp/ping_clean", "${PING_CLEAN_PATH:-/tmp/ping_clean}"),
        encoding="utf-8",
    )
    install_copy.chmod(0o755)

    for name in INSTALL_COPY_SCRIPTS:
        source = ROOT / "security_helpers" / name
        destination = script_dir / name
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        destination.chmod(0o755)

    return install_copy


def test_check_egress_reports_missing_ping_as_audit_error():
    res = run(
        ["/bin/sh", str(CHECK_EGRESS), "8.8.8.8"],
        shell=False,
        env={"PATH": "/nonexistent"},
    )

    assert res.code == 2
    assert "ping is required" in res.output


def test_check_egress_https_treats_live_tcp_path_as_reachable():
    server, thread = _start_http_server()
    try:
        port = server.server_address[1]
        res = run(
            ["/bin/sh", str(CHECK_EGRESS), "--https", "--port", str(port), "127.0.0.1"],
            shell=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert res.code == 0, res.output


def test_healthcheck_requires_check_url():
    res = run(
        ["/bin/sh", str(HEALTHCHECK)],
        shell=False,
        env={"CHECK_URL": ""},
    )

    assert res.code == 1
    assert "CHECK_URL must be set" in res.output


def test_healthcheck_succeeds_with_local_http_target():
    server, thread = _start_http_server()
    try:
        port = server.server_address[1]
        res = run(
            ["/bin/sh", str(HEALTHCHECK)],
            shell=False,
            env={"CHECK_URL": f"http://127.0.0.1:{port}/"},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert res.code == 0, res.output


def test_active_monitor_treats_blocked_probe_as_healthy(tmp_path):
    fake_check = _write_executable(tmp_path / "check_egress.sh", "#!/bin/sh\nexit 1\n")

    for tool in ("ping", "curl"):
        _write_executable(tmp_path / tool, "#!/bin/sh\nexit 0\n")

    entrypoint_copy = _copy_monitor_entrypoint(tmp_path, fake_check)

    res = run(
        ["/bin/sh", str(entrypoint_copy), "/bin/sh", "-c", "sleep 2"],
        shell=False,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "FORBIDDEN_IPS": "8.8.8.8",
            "FORBIDDEN_HTTPS_TARGETS": "",
            "HEARTBEAT_INTERVAL": "1",
            "HEARTBEAT_LOG_MODULO": "1",
        },
        timeout=10,
    )

    assert res.code == 0, res.output
    assert "Status: PUBLIC DNS UNREACHABLE" in res.output
    assert "ISOLATION MONITOR FAILURE" not in res.output


def test_active_monitor_fails_closed_on_audit_error(tmp_path):
    fake_check = _write_executable(tmp_path / "check_egress.sh", "#!/bin/sh\nexit 2\n")
    _write_executable(tmp_path / "ping", "#!/bin/sh\nexit 0\n")
    entrypoint_copy = _copy_monitor_entrypoint(tmp_path, fake_check)

    res = run(
        ["/bin/sh", str(entrypoint_copy), "sleep", "30"],
        shell=False,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "FORBIDDEN_IPS": "8.8.8.8",
            "FORBIDDEN_HTTPS_TARGETS": "",
            "HEARTBEAT_INTERVAL": "1",
            "HEARTBEAT_LOG_MODULO": "1",
        },
        timeout=10,
    )

    assert res.code == 1, res.output
    assert "ISOLATION MONITOR FAILURE" in res.output


def test_active_monitor_treats_https_reachability_as_breach(tmp_path):
    fake_check = _write_executable(
        tmp_path / "check_egress.sh",
        "#!/bin/sh\n"
        'if [ "${1:-}" = "--https" ]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    _write_executable(tmp_path / "curl", "#!/bin/sh\nexit 0\n")
    entrypoint_copy = _copy_monitor_entrypoint(tmp_path, fake_check)

    res = run(
        ["/bin/sh", str(entrypoint_copy), "sleep", "30"],
        shell=False,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "FORBIDDEN_IPS": "",
            "FORBIDDEN_HTTPS_TARGETS": "1.1.1.1",
            "FORBIDDEN_HTTPS_PORT": "443",
            "HEARTBEAT_INTERVAL": "1",
            "HEARTBEAT_LOG_MODULO": "1",
        },
        timeout=10,
    )

    assert res.code == 0, res.output
    assert "Unauthorized https egress detected" in res.output
    assert "EMERGENCY SHUTDOWN" in res.output


def test_nginx_debug_dump_stays_quiet_by_default():
    res = run(
        ["/bin/sh", str(NGINX_DEBUG_DUMP), "Ingress Configurer", "", "Configuration complete."],
        shell=False,
        env={"DEBUG_NGINX_CONFIG_DUMP": "false"},
    )

    assert res.code == 0, res.output
    assert "Configuration complete." in res.output
    assert "Dumping final nginx configuration" not in res.output


def test_nginx_debug_dump_prints_config_and_extra_file_when_enabled(tmp_path):
    extra_config = tmp_path / "generated.conf"
    extra_config.write_text("worker_connections 64;\n", encoding="utf-8")
    _write_executable(tmp_path / "nginx", '#!/bin/sh\necho "fake nginx config"\n')

    res = run(
        ["/bin/sh", str(NGINX_DEBUG_DUMP), "Ingress Configurer", str(extra_config)],
        shell=False,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DEBUG_NGINX_CONFIG_DUMP": "true",
        },
    )

    assert res.code == 0, res.output
    assert "Dumping final nginx configuration" in res.output
    assert "fake nginx config" in res.output
    assert f"Contents of {extra_config}:" in res.output
    assert "worker_connections 64;" in res.output


def test_install_security_helpers_sanitizes_ping_and_installs_scripts(tmp_path):
    install_copy = _copy_install_script_tree(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    install_bin = tmp_path / "installed-bin"
    install_bin.mkdir()
    ping_clean = tmp_path / "ping_clean"
    apt_log = tmp_path / "apt.log"

    _write_executable(
        fake_bin / "apt-get",
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$APT_GET_LOG"\n'
        "exit 0\n",
    )
    ping_path = _write_executable(fake_bin / "ping", "#!/bin/sh\nexit 0\n")
    ping_path.chmod(0o4755)

    res = run(
        ["/bin/sh", str(install_copy)],
        shell=False,
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "INSTALL_BIN_DIR": str(install_bin),
            "PING_CLEAN_PATH": str(ping_clean),
            "APT_GET_LOG": str(apt_log),
        },
    )

    assert res.code == 0, res.output
    assert "Sanitizing" in res.output
    assert "Complete." in res.output
    assert not (ping_path.stat().st_mode & stat.S_ISUID), "ping retained SUID bit after install"
    for script_name in INSTALL_COPY_SCRIPTS:
        installed = install_bin / script_name
        assert installed.exists(), f"{script_name} was not installed"
        assert os.access(installed, os.X_OK), f"{script_name} is not executable after install"
    assert "update" in apt_log.read_text(encoding="utf-8")
    assert "install -y --no-install-recommends iputils-ping curl" in apt_log.read_text(
        encoding="utf-8"
    )


def test_install_security_helpers_warns_when_ping_missing(tmp_path):
    install_copy = _copy_install_script_tree(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    install_bin = tmp_path / "installed-bin"
    install_bin.mkdir()

    _write_executable(fake_bin / "apt-get", "#!/bin/sh\nexit 0\n")

    res = run(
        ["/bin/sh", str(install_copy)],
        shell=False,
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "INSTALL_BIN_DIR": str(install_bin),
            "PING_CLEAN_PATH": str(tmp_path / 'ping_clean'),
            "PING_BINARY_NAME": "missing-ping",
        },
    )

    assert res.code == 0, res.output
    assert "'ping' binary not found" in res.output
    for script_name in INSTALL_COPY_SCRIPTS:
        assert (install_bin / script_name).exists(), f"{script_name} was not installed"
