from collections.abc import Iterable
from pathlib import Path

from tests.helpers.commands import CmdResult, run

PUBLIC_DNS_TARGETS = ("8.8.8.8", "1.1.1.1", "9.9.9.9")
PUBLIC_HTTPS_TARGETS = ("1.1.1.1",)
CHECK_EGRESS = Path(__file__).resolve().parents[2] / "security_helpers" / "check_egress.sh"


def security_alert_logged(container: str) -> bool:
    res = run(["docker", "logs", container], shell=False)
    return "SECURITY ALERT" in res.output


def bridge_gateway() -> str:
    res = run(
        [
            "docker",
            "network",
            "inspect",
            "bridge",
            "--format",
            "{{range .IPAM.Config}}{{.Gateway}}{{end}}",
        ],
        shell=False,
    )
    return res.stdout.strip()


def container_pid(container: str) -> str:
    res = run(["docker", "inspect", "-f", "{{.State.Pid}}", container], shell=False)
    return res.stdout.strip()


def container_running(container: str) -> bool:
    res = run(["docker", "inspect", "-f", "{{.State.Running}}", container], shell=False)
    return res.stdout.strip() == "true"


def attach_container_to_bridge(container: str) -> CmdResult:
    return run(["docker", "network", "connect", "bridge", container], shell=False)


def detach_container_from_bridge(container: str) -> CmdResult:
    return run(["docker", "network", "disconnect", "bridge", container], shell=False)


def inject_default_route(pid: str, gateway: str) -> CmdResult:
    return run(
        ["nsenter", "-t", pid, "-n", "ip", "route", "replace", "default", "via", gateway],
        shell=False,
    )


def probe_public_egress(
    container: str,
    *,
    targets: Iterable[str] = PUBLIC_DNS_TARGETS,
) -> tuple[bool, bool]:
    for target_ip in targets:
        res = run(
            ["docker", "exec", container, "/usr/local/bin/check_egress.sh", target_ip],
            shell=False,
        )
        if res.code == 0:
            return True, False
        if res.code == 1:
            continue

        if not container_running(container):
            return False, True

    return False, False


def probe_public_https_egress(
    pid: str,
    *,
    targets: Iterable[str] = PUBLIC_HTTPS_TARGETS,
    port: int = 443,
) -> tuple[bool, bool]:
    for target_ip in targets:
        res = run(
            [
                "nsenter",
                "-t",
                pid,
                "-n",
                "/bin/sh",
                str(CHECK_EGRESS),
                "--https",
                "--port",
                str(port),
                target_ip,
            ],
            shell=False,
        )
        if res.code == 0:
            return True, False
        if res.code == 1:
            continue

        output = res.output
        if "No such file or directory" in output or "target pid not found" in output:
            return False, True

    return False, False
