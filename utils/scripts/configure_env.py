#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.stack.startup import run_wizard


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactive .env wizard for UKB-GPT mode/feature/app settings."
    )
    parser.add_argument(
        "--root-dir",
        default=str(REPO_ROOT),
        help="Repository root containing compose/schema.toml (default: repo root).",
    )
    parser.add_argument(
        "--prefill-env",
        default="",
        help="Optional existing .env path to prefill values and skip prompts for already-set keys.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite any existing .env instead of extending it.",
    )
    args = parser.parse_args()
    prefill_path = Path(args.prefill_env).expanduser().resolve() if args.prefill_env else None
    existing_env_mode = "overwrite" if args.overwrite else "ask"
    return run_wizard(
        Path(args.root_dir).resolve(),
        prefill_env_path=prefill_path,
        existing_env_mode=existing_env_mode,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
