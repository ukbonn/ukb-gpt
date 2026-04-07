#!/usr/bin/env python3

import argparse
import os
import sys
import subprocess
import shutil
import datetime
from pathlib import Path

_START_ROOT_DIR = str(Path(__file__).resolve().parent)
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    raw = os.getenv(name)
    return raw is not None and raw.strip().lower() in _TRUE_VALUES


def _repo_venv_python(root_dir: str = _START_ROOT_DIR) -> str:
    return os.path.join(root_dir, ".venv", "bin", "python")


def _running_in_repo_venv(
    root_dir: str = _START_ROOT_DIR,
    *,
    current_prefix: str | None = None,
    current_executable: str | None = None,
) -> bool:
    repo_venv_root = os.path.realpath(os.path.join(root_dir, ".venv"))
    active_prefix = os.path.realpath(current_prefix or sys.prefix)
    if active_prefix == repo_venv_root:
        return True

    active_executable = os.path.abspath(current_executable or sys.executable)
    return active_executable == os.path.abspath(_repo_venv_python(root_dir))


def _prefer_repo_venv_python(root_dir: str = _START_ROOT_DIR) -> None:
    """
    Re-exec in the repository's .venv interpreter when available.
    This keeps runtime tools (pytest/pyyaml) aligned with project deps.
    """
    if _env_flag("UKBGPT_SKIP_VENV_REEXEC"):
        return

    venv_python = _repo_venv_python(root_dir)
    if not (os.path.isfile(venv_python) and os.access(venv_python, os.X_OK)):
        return
    if _running_in_repo_venv(root_dir):
        return

    print(f"ℹ️  Re-launching with repository virtualenv interpreter: {venv_python}")
    try:
        os.execv(venv_python, [venv_python] + sys.argv)
    except OSError as exc:
        print(f"⚠️  Warning: Failed to switch to .venv interpreter: {exc}")
        print("   Continuing with current interpreter.")


# Bootstrap into the repo venv before importing modules that rely on its packages.
_prefer_repo_venv_python()

from utils.stack.startup import (
    ROOT_DIR,
    apply_env_file,
    default_env_file_path,
    discover_backends,
    plan_service_startup,
    prepare_startup_config,
    env_bool,
    env_str,
    print_ukbgpt_banner,
    run_wizard,
    run_command,
    setup_logging,
    start_services,
)
from utils.stack.launch import BackendDiscovery

GPU_PROBE_CMD = [
    "docker",
    "run",
    "--rm",
    "--gpus",
    "all",
    "nvidia/cuda:12.4.1-base-ubuntu22.04",
    "nvidia-smi",
]

def _probe_docker_gpu_support():
    probe = subprocess.run(GPU_PROBE_CMD, capture_output=True, text=True)
    return probe.returncode == 0, probe.stderr.strip()

def _check_docker_runtime():
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode == 0:
        return True

    print("❌ Error: Docker runtime is not available.")
    stderr = (probe.stdout or "").lower() + (probe.stderr or "").lower()
    if "permission denied" in stderr:
        print("   This may be due to missing permissions to access the Docker daemon socket.")
        print("   Fix: add the current user to the docker group or run start.py with elevated privileges.")
    elif "is the docker daemon running" in stderr:
        print("   This may be because the Docker daemon is not running.")
        print("   Fix: start Docker and retry.")
    else:
        print("   Verify Docker is installed and running.")
    if probe.stderr:
        print(f"   Details: {probe.stderr.strip()}")
    return False


def _load_command_env_file(env_file: str | None, *, required: bool) -> None:
    if env_file is None:
        return
    resolved = os.path.abspath(os.path.expanduser(env_file))
    if not os.path.isfile(resolved):
        if required:
            print(f"❌ Error: env file not found: {resolved}")
            sys.exit(1)
        return
    loaded = apply_env_file(resolved, override=False)
    print(
        f"ℹ️  Loaded {len(loaded)} values from {resolved} "
        "(shell exports take precedence)."
    )


def _ensure_docker_ready() -> None:
    if shutil.which("docker") is None:
        print("❌ Error: Docker is not installed.")
        sys.exit(1)
    if not _check_docker_runtime():
        sys.exit(1)


def _validate_selected_model_runtime(startup_config) -> None:
    model_compose_text_parts = []
    for resolved_deployment in startup_config.deployment_bundle.resolved_deployments:
        try:
            with open(resolved_deployment.generated_compose_path, "r", encoding="utf-8") as handle:
                model_compose_text_parts.append(handle.read())
        except OSError:
            continue
    model_compose_text = "\n".join(model_compose_text_parts)

    model_requires_gpu = "driver: nvidia" in model_compose_text or "capabilities: [gpu]" in model_compose_text
    if model_requires_gpu:
        gpu_probe_ok, gpu_probe_error = _probe_docker_gpu_support()
        if not gpu_probe_ok:
            print("❌ Error: Selected model requires NVIDIA GPUs, but Docker GPU access test failed.")
            print("   Action: Install/enable NVIDIA Container Toolkit (or CDI), restart Docker, then verify:")
            print("   docker info | grep -E 'Runtimes|Default Runtime'")
            print("   docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi")
            if gpu_probe_error:
                print(f"   Probe error: {gpu_probe_error}")
            sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UKB-GPT stack management CLI.")
    subparsers = parser.add_subparsers(dest="command")

    up_parser = subparsers.add_parser("up", help="Validate configuration and start the stack.")
    up_parser.add_argument(
        "--env-file",
        default="",
        help="Optional env file to load before validation/startup (defaults to .env when present).",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate configuration without starting services.")
    validate_parser.add_argument(
        "--env-file",
        default="",
        help="Optional env file to load before validation (defaults to .env when present).",
    )

    wizard_parser = subparsers.add_parser("wizard", help="Run the interactive configuration wizard.")
    wizard_parser.add_argument(
        "--prefill-env",
        default="",
        help="Optional env file to reuse as prefilled answers.",
    )
    wizard_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing .env instead of extending it.",
    )
    wizard_parser.add_argument(
        "--extend",
        action="store_true",
        help="Extend an existing .env without asking how to proceed.",
    )
    wizard_parser.add_argument(
        "--start",
        action="store_true",
        help="Run startup automatically after the wizard completes.",
    )

    return parser


def _safe_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _print_access_endpoints(batch_mode: bool, discovery: BackendDiscovery | None = None) -> None:
    server_name = env_str("SERVER_NAME", "localhost") or "localhost"
    batch_port = env_str("BATCH_CLIENT_LISTEN_PORT", "30000") or "30000"
    direct_start = env_str("BATCH_CLIENT_DIRECT_PORT_START", "30001") or "30001"
    direct_end = env_str("BATCH_CLIENT_DIRECT_PORT_END", "30032") or "30032"
    metrics_enabled = env_bool("ENABLE_INTERNAL_METRICS")
    dictation_enabled = env_bool("ENABLE_DICTATION_APP")
    cohort_feasibility_enabled = env_bool("ENABLE_COHORT_FEASIBILITY_APP")
    acl = env_str("NGINX_ACL_ALLOW_LIST")

    print("\n🌐 Access Endpoints")
    if batch_mode:
        additional_api = env_str("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ADDRESS")
        additional_embedding_api = env_str("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ADDRESS")
        additional_api_enabled = env_bool("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_API_ENABLED")
        additional_embedding_api_enabled = env_bool("BATCH_CLIENT_MODE_ADDITIONAL_LOCAL_EMBEDDING_API_ENABLED")
        llm_workers = []
        if discovery:
            llm_workers = list(discovery.llm.workers)
        if not llm_workers and env_str("LLM_BACKEND_NODES"):
            llm_workers = [n.strip().split(":")[0] for n in env_str("LLM_BACKEND_NODES").split(",") if n.strip()]

        start_port = _safe_int(direct_start, 30001)
        end_port = _safe_int(direct_end, 30032)
        next_port = start_port
        direct_worker_mappings = []
        for worker in llm_workers:
            if next_port > end_port:
                break
            direct_worker_mappings.append((next_port, worker))
            next_port += 1

        additional_api_direct_port = None
        if additional_api and next_port <= end_port:
            additional_api_direct_port = next_port

        print(f"   Batch API: http://127.0.0.1:{batch_port}/v1/")
        print(f"   Runtime Discovery: http://127.0.0.1:{batch_port}/v1/ukbgpt/runtime")
        if cohort_feasibility_enabled:
            print(f"   Cohort Feasibility: http://127.0.0.1:{batch_port}/feasibility/")
        print("   Router Behavior: /v1/* on 30000 is least_conn across local workers")
        if additional_api_enabled or additional_embedding_api_enabled:
            print(
                "                    plus api_egress when additional API targets are configured "
                f"(LLM={'on' if additional_api_enabled else 'off'}, Embedding={'on' if additional_embedding_api_enabled else 'off'})"
            )

        if direct_worker_mappings:
            workers_summary = ", ".join([f"{port}->{worker}" for port, worker in direct_worker_mappings])
            print(f"   Direct Worker Ports: {workers_summary} (/v1)")
        else:
            print(f"   Direct Worker Port Range: 127.0.0.1:{direct_start}-{direct_end} (/v1)")

        if additional_api_enabled and additional_api:
            if additional_api_direct_port is not None:
                print(f"   Direct Additional API: {additional_api_direct_port}->api_egress (/v1)")
            else:
                print(
                    "   Direct Additional API: not exposed "
                    "(direct range exhausted by worker mappings)"
                )

        print("   Note: Batch mode does not expose HTTPS WebUI.")
        return

    print(f"   WebUI: https://{server_name}/")
    if metrics_enabled:
        print(f"   Grafana: https://{server_name}/grafana/")
    if dictation_enabled:
        print(f"   Dictation: https://{server_name}/dictation/")

    if server_name in {"localhost", "127.0.0.1"} and "127.0.0.1" not in acl:
        print("   Hint: NGINX_ACL_ALLOW_LIST does not include 127.0.0.1.")
        print("         Add 'allow 127.0.0.1;' for localhost browser access.")


def _run_up_command(env_file: str | None = None):
    _prefer_repo_venv_python()
    _load_command_env_file(env_file, required=bool(env_file))

    # 0. Global Logging Setup
    # This also sets sys.stdout/stderr to a Tee object
    log_file = setup_logging(announce=False, show_session_header=False)
    print_ukbgpt_banner(
        "Standalone Startup",
        "Validate configuration, enforce host isolation, and launch services.",
    )
    print(f"📝 Logging entire startup session to: {log_file}")
    print(f"Date: {datetime.datetime.now()}")
    
    # 1. Pre-flight Checks (Docker existence)
    _ensure_docker_ready()

    # 2. Configuration Validation
    startup_config = prepare_startup_config()
    _validate_selected_model_runtime(startup_config)
    
    # 3.5 Dynamic Backend Discovery
    discovery = discover_backends(
        startup_config.deployment_bundle.llm_compose_flags,
        startup_config.deployment_bundle.embedding_compose_flags,
        startup_config.deployment_bundle.stt_compose_flags,
        startup_config.deployment_bundle.tts_compose_flags,
        startup_config.deployment_bundle.resolved_deployments,
    )
    runtime_services = list(discovery.runtime_services)
    
    # 4. Building & Service Creation
    # Build first, then create stopped containers so the firewall still lands
    # before any workload starts. Splitting the steps avoids compose hangs seen
    # with `up --build --no-start` on some Docker Compose versions.
    is_test = env_bool("IS_INTEGRATION_TEST")
    launch_services = startup_config.core_services + runtime_services
    launch_plan = plan_service_startup(
        startup_config.core_services,
        discovery,
        startup_config.deployment_bundle.resolved_deployments,
    )

    print("\n--> Building Service Images...")
    build_cmd = [
        "docker",
        "compose",
        "--progress",
        "plain",
        *startup_config.compose_args,
        "build",
        *launch_services,
    ]

    run_command(build_cmd, silent=is_test)

    print("\n--> Creating Services (Paused)...")
    create_cmd = [
        "docker",
        "compose",
        *startup_config.compose_args,
        "up",
        "--no-start",
        "--remove-orphans",
        "-y",
        *launch_services,
    ]

    run_command(create_cmd, silent=is_test)

    # 5. Host-Level Firewall (L2 Isolation)
    # This modifies the Kernel's iptables. Sudo is required.
    print("🔒 [Firewall] Applying Host-Level Isolation Rules (needs sudo)...")
    firewall_script = os.path.join(ROOT_DIR, "security_helpers", "apply_host_firewall.py")
    firewall_cmd = ["sudo", "env"]
    expect_egress_bridge = env_str("UKBGPT_EXPECT_EGRESS_BRIDGE")
    firewall_egress_rules = env_str("UKBGPT_FIREWALL_EGRESS_RULES")
    egress_target_ips = env_str("EGRESS_TARGET_IPS")
    egress_target_ip = env_str("EGRESS_TARGET_IP") or env_str("LDAP_TARGET_IP")
    ldap_target_ip = env_str("LDAP_TARGET_IP")
    if expect_egress_bridge:
        firewall_cmd.append(f"UKBGPT_EXPECT_EGRESS_BRIDGE={expect_egress_bridge}")
    if firewall_egress_rules:
        firewall_cmd.append(f"UKBGPT_FIREWALL_EGRESS_RULES={firewall_egress_rules}")
    if egress_target_ips:
        firewall_cmd.append(f"EGRESS_TARGET_IPS={egress_target_ips}")
    if egress_target_ip:
        firewall_cmd.append(f"EGRESS_TARGET_IP={egress_target_ip}")
    if ldap_target_ip:
        firewall_cmd.append(f"LDAP_TARGET_IP={ldap_target_ip}")
    firewall_cmd.extend([sys.executable, firewall_script])
    try:
        subprocess.run(firewall_cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"❌ Error applying firewall rules: {exc}")
        sys.exit(1)
    
    # 6. Launch
    print("\n--> Starting Services in Protected Network...")
    start_services(startup_config.compose_args, launch_plan, startup_config.batch_mode)

    _print_access_endpoints(startup_config.batch_mode, discovery=discovery)

    print(f"\n✅ Startup Complete. Time: {datetime.datetime.now()}")
    return 0


def _run_validate_command(env_file: str | None = None) -> int:
    _prefer_repo_venv_python()
    _load_command_env_file(env_file, required=bool(env_file))
    setup_logging()
    _ensure_docker_ready()
    startup_config = prepare_startup_config()
    _validate_selected_model_runtime(startup_config)
    print(f"\n✅ Configuration validation passed. Time: {datetime.datetime.now()}")
    return 0


def _run_wizard_command(args) -> int:
    if args.overwrite and args.extend:
        print("❌ Error: --overwrite and --extend are mutually exclusive.")
        return 1

    prefill_path = Path(args.prefill_env).expanduser().resolve() if args.prefill_env else None
    if args.overwrite:
        existing_env_mode = "overwrite"
    elif args.extend:
        existing_env_mode = "prefill"
    else:
        existing_env_mode = "ask"

    result = run_wizard(
        ROOT_DIR,
        prefill_env_path=prefill_path,
        existing_env_mode=existing_env_mode,
    )
    if result != 0 or not args.start:
        return result

    return _run_up_command(default_env_file_path(ROOT_DIR))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "up"

    if command == "wizard":
        return _run_wizard_command(args)

    explicit_env_file = bool(getattr(args, "env_file", ""))
    env_file = args.env_file if explicit_env_file else default_env_file_path(ROOT_DIR)
    if not explicit_env_file and not os.path.isfile(env_file):
        env_file = None
    if command == "validate":
        return _run_validate_command(env_file)
    if command == "up":
        return _run_up_command(env_file)

    parser.error(f"Unknown command: {command}")
    return 2

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user. Exiting...")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n🔥 Unhandled error during startup: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
