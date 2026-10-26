"""CLI configuration paths and optional dotenv loading."""

import os
from pathlib import Path


def config_directory() -> Path:
    """Use the user's config directory independently of the installed wheel."""
    root = os.environ.get("XDG_CONFIG_HOME")
    return (Path(root).expanduser() if root else Path.home() / ".config") / "chatterpal"


def default_env_file() -> Path:
    configured = os.environ.get("CHATTERPAL_ENV_FILE")
    if configured:
        return Path(configured).expanduser()
    local = Path.cwd() / ".env"
    return local if local.is_file() else config_directory() / ".env"


def load_environment(env_file: Path | None = None) -> None:
    """Load a selected dotenv file without replacing existing process variables."""
    selected = env_file.expanduser() if env_file else default_env_file()
    explicit = env_file is not None or bool(os.environ.get("CHATTERPAL_ENV_FILE"))
    if not selected.is_file():
        if explicit:
            raise FileNotFoundError(f"Environment file not found: {selected}")
        return

    # Help and setup do not require python-dotenv or any provider packages.
    from dotenv import load_dotenv

    load_dotenv(selected, override=False)


def runtime_settings(env_file: Path | None = None):
    load_environment(env_file)
    from chatterpal.config.settings import Settings

    # CLI loading above defines file discovery; process variables take precedence.
    return Settings(_env_file=None)
