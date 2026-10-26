"""Inspect an installation and explicitly initialize private user configuration."""

import argparse
import importlib.metadata
import os
import sys
from pathlib import Path
from typing import Sequence

from .config import config_directory, runtime_settings

_ENV_TEMPLATE = """# ChatterPal user configuration. Process environment variables take precedence.
# Start a cloud API with chatterpal-run --mode api after installing
# chatterpal[api,cloud] and selecting/configuring the providers below.

ASR_PROVIDER=whisper
TTS_PROVIDER=edge
LLM_PROVIDER=alibaba
DASHSCOPE_API_KEY=
OPENAI_API_KEY=
ALIYUN_ASR_APP_KEY=
ALIYUN_ASR_TOKEN=

API_HOST=127.0.0.1
API_PORT=8010
GRADIO_PORT=7860
GRADIO_SHARE=false
WHISPER_MODEL=base
LOG_LEVEL=INFO
"""


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect ChatterPal or explicitly create private user configuration.",
        epilog=(
            "No command defaults to a read-only check. Dependencies are never installed "
            "by this command. Use pip install 'chatterpal[api,cloud]' for cloud APIs "
            "or uv sync --locked in a development checkout."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("check", "verify", "env", "dirs", "help"),
        default="check",
        help="check installed versions, verify config, create env file, or create user dirs",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Config to create/verify (creation defaults to the user's config directory)",
    )
    return parser


def check_installation() -> int:
    print(f"Python {sys.version.split()[0]} (requires >=3.12)")
    for distribution in (
        "chatterpal",
        "fastapi",
        "uvicorn",
        "openai",
        "dashscope",
        "edge-tts",
        "openai-whisper",
        "praat-parselmouth",
        "gradio",
        "pyaudio",
    ):
        try:
            version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            version = "not installed (optional)"
        print(f"{distribution}: {version}")
    return 0 if sys.version_info >= (3, 12) else 1


def create_environment_file(path: Path) -> None:
    """Create a 0600 dotenv file exclusively; preserve all existing files/links."""
    path = path.expanduser()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(_ENV_TEMPLATE)
    print(f"Created configuration: {path}")
    print("Edit the provider credentials before starting the selected runtime.")


def create_user_directories() -> None:
    data_root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    cache_root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    for directory in (
        config_directory(),
        data_root.expanduser() / "chatterpal",
        cache_root.expanduser() / "chatterpal",
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        print(f"Directory available: {directory}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    if args.command == "help":
        parser.print_help()
        return 0
    try:
        if args.command == "check":
            return check_installation()
        if args.command == "env":
            path = args.env_file or Path(
                os.environ.get("CHATTERPAL_ENV_FILE") or config_directory() / ".env"
            )
            create_environment_file(path)
        elif args.command == "dirs":
            create_user_directories()
        elif args.command == "verify":
            settings = runtime_settings(args.env_file)
            errors = settings.get_configuration_errors()
            if errors:
                for error in errors:
                    print(error, file=sys.stderr)
                return 1
            print("Configuration is valid. No provider calls were made.")
        return 0
    except FileExistsError as exc:
        print(
            f"Configuration already exists; left unchanged: {exc.filename}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
