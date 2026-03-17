#!/usr/bin/env python3

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from utils.stack.startup import ROOT_DIR, env_bool, run_command

DEFAULT_PROJECT_NAME = "ukbgpt"
PROJECT_NAME_PATTERN = re.compile(r"^\s*name:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$")


def _prefer_repo_venv_python() -> None:
    """
    Re-exec in the repository's .venv interpreter when available.
    This keeps runtime tools aligned with project deps.
    """
    if env_bool("UKBGPT_SKIP_VENV_REEXEC"):
        return

    venv_python = os.path.join(ROOT_DIR, ".venv", "bin", "python")
    if not (os.path.isfile(venv_python) and os.access(venv_python, os.X_OK)):
        return

    current_python = os.path.realpath(sys.executable)
    target_python = os.path.realpath(venv_python)
    if current_python == target_python:
        return

    print(f"Info: Re-launching with repository virtualenv interpreter: {venv_python}")
    try:
        os.execv(venv_python, [venv_python] + sys.argv)
    except OSError as exc:
        print(f"Warning: Failed to switch to .venv interpreter: {exc}")
        print("Continuing with current interpreter.")


def _check_docker_runtime() -> bool:
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode == 0:
        return True

    print("Error: Docker runtime is not available.")
    stderr = (probe.stdout or "").lower() + (probe.stderr or "").lower()
    if "permission denied" in stderr:
        print("This may be due to missing permissions to access the Docker daemon socket.")
        print("Fix: add the current user to the docker group or run stop.py with elevated privileges.")
    elif "is the docker daemon running" in stderr:
        print("This may be because the Docker daemon is not running.")
        print("Fix: start Docker and retry.")
    else:
        print("Verify Docker is installed and running.")
    if probe.stderr:
        print(f"Details: {probe.stderr.strip()}")
    return False


def _ensure_docker_ready() -> None:
    if shutil.which("docker") is None:
        print("Error: Docker is not installed.")
        sys.exit(1)
    if not _check_docker_runtime():
        sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stop the UKB-GPT stack.")
    parser.add_argument(
        "--volumes",
        action="store_true",
        help="Also remove project-scoped Docker volumes created by the stack.",
    )
    return parser


def _base_compose_path() -> str:
    path = os.path.join(ROOT_DIR, "compose", "base.yml")
    if not os.path.isfile(path):
        print(f"Error: required compose file missing: {path}")
        sys.exit(1)
    return path


def _project_name() -> str:
    base_compose = Path(_base_compose_path())
    try:
        for line in base_compose.read_text(encoding="utf-8").splitlines():
            match = PROJECT_NAME_PATTERN.match(line)
            if match:
                return match.group(1)
    except OSError as exc:
        print(f"Warning: Failed to read {base_compose}: {exc}")

    print(f"Warning: Could not determine compose project name from {base_compose}.")
    print(f"Falling back to default project name: {DEFAULT_PROJECT_NAME}")
    return DEFAULT_PROJECT_NAME


def _list_project_resources(resource_kind: str, project_name: str) -> list[str]:
    result = run_command(
        [
            "docker",
            resource_kind,
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project_name}",
            "--format",
            "{{.Name}}",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"Warning: Failed to list project {resource_kind}s for {project_name}.")
        if result.stderr:
            print(result.stderr.strip())
        return []

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _remove_project_resources(resource_kind: str, project_name: str) -> int:
    names = _list_project_resources(resource_kind, project_name)
    if not names:
        return 0

    print(f"Cleaning up project {resource_kind}s: {', '.join(names)}")
    failures = 0
    for name in names:
        result = run_command(
            ["docker", resource_kind, "rm", name],
            check=False,
            capture_output=True,
        )
        if result.returncode == 0:
            continue

        failures += 1
        print(f"Warning: Failed to remove {resource_kind} {name}.")
        if result.stderr:
            print(result.stderr.strip())

    return failures


def _run_down_command(*, remove_volumes: bool) -> int:
    _prefer_repo_venv_python()
    _ensure_docker_ready()

    base_compose = _base_compose_path()
    project_name = _project_name()

    print(f"Stopping Docker Compose project: {project_name}")
    run_command(
        [
            "docker",
            "compose",
            "-f",
            base_compose,
            "down",
            "--remove-orphans",
        ]
    )

    failures = _remove_project_resources("network", project_name)
    if remove_volumes:
        failures += _remove_project_resources("volume", project_name)

    if failures:
        print(f"Shutdown completed with {failures} cleanup warning(s).")
        return 1

    print("Shutdown complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _run_down_command(remove_volumes=args.volumes)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting...")
        sys.exit(130)
    except Exception as exc:
        print(f"\nUnhandled error during shutdown: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
