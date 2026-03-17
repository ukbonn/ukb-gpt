import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class CmdResult:
    cmd: str
    stdout: str
    stderr: str
    code: int

    @property
    def output(self) -> str:
        return f"{self.stdout}{self.stderr}".strip()


def run(
    cmd,
    *,
    shell: bool = True,
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    timeout: Optional[int] = None,
    stdin: Optional[str] = None,
    stream: bool = False,
) -> CmdResult:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    if not stream:
        result = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            env=full_env,
            cwd=cwd,
            timeout=timeout,
            input=stdin,
        )
        return CmdResult(
            cmd=" ".join(cmd) if isinstance(cmd, list) else cmd,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            code=result.returncode,
        )

    if stdin is not None:
        raise ValueError("stdin is not supported when stream=True")

    proc = subprocess.Popen(
        cmd,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=full_env,
        cwd=cwd,
    )
    output_lines = []
    assert proc.stdout is not None
    for line in proc.stdout:
        output_lines.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    proc.wait(timeout=timeout)
    combined = "".join(output_lines)
    return CmdResult(
        cmd=" ".join(cmd) if isinstance(cmd, list) else cmd,
        stdout=combined,
        stderr="",
        code=proc.returncode or 0,
    )


def assert_ok(result: CmdResult, message: str) -> None:
    if result.code != 0:
        detail = result.output
        if detail:
            raise AssertionError(f"{message}\n\nCommand: {result.cmd}\n{detail}")
        raise AssertionError(f"{message}\n\nCommand: {result.cmd}")


def docker_exec(container: str, args: list[str]) -> CmdResult:
    return run(["docker", "exec", container, *args], shell=False)


def docker_exec_python(container: str, script: str) -> CmdResult:
    return run(
        ["docker", "exec", "-i", container, "python3", "-"],
        shell=False,
        stdin=script,
    )


def docker_exec_sh(container: str, script: str) -> CmdResult:
    return run(["docker", "exec", container, "sh", "-lc", script], shell=False)


def docker_ps_name(name: str) -> CmdResult:
    return run(
        ["docker", "ps", "--filter", f"name={name}", "--format", "{{.Names}}"],
        shell=False,
    )


def docker_nginx_config(container: str) -> str:
    return docker_exec(container, ["nginx", "-T"]).output


def retry_until(
    fn: Callable[[], bool],
    *,
    attempts: int,
    delay_seconds: float,
) -> bool:
    for _ in range(attempts):
        if fn():
            return True
        time.sleep(delay_seconds)
    return False
