#!/usr/bin/env python3
"""Compatibility wrapper for the published ChatterPal launcher."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chatterpal.cli.run import create_parser, main


if __name__ == "__main__":
    raise SystemExit(main())
