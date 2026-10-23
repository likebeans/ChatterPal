#!/usr/bin/env python3
"""Check the real Settings configuration without downloading models or calling APIs."""

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from chatterpal.config.settings import Settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / ".env.example",
        help="Environment file to validate (default: shipped .env.example)",
    )
    parser.add_argument(
        "--require-credentials",
        action="store_true",
        help="Also require credentials for the selected providers; no network requests",
    )
    args = parser.parse_args(argv)
    env_file = args.env_file.resolve()
    if not env_file.is_file():
        print(f"ERROR: environment file does not exist: {env_file}")
        return 1

    try:
        values = dotenv_values(env_file, encoding="utf-8")
        unknown = sorted(
            key for key in values if key.lower() not in Settings.model_fields
        )
        if unknown:
            print("Ignored environment keys: " + ", ".join(unknown))
            if env_file == (PROJECT_ROOT / ".env.example").resolve():
                print(
                    "ERROR: the shipped template must only advertise supported Settings fields"
                )
                return 1
        settings = Settings(_env_file=env_file)
    except ValidationError as exc:
        # Field names and error types are enough to diagnose types; never print inputs.
        for error in exc.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in error["loc"])
            print(f"ERROR: {field}: {error['type']}")
        return 1
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: cannot read environment file ({type(exc).__name__})")
        return 1

    print(f"Settings loaded: {env_file}")
    print("Process environment variables take precedence over this file.")
    if args.require_credentials:
        errors = settings.get_configuration_errors()
        for error in errors:
            print(f"ERROR: {error}")
        if errors:
            return 1
        print(
            "Required provider credentials are configured (not tested against remote services)."
        )
    else:
        print(
            "Template types are valid. Fill in provider credentials before starting the app."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
