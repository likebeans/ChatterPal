#!/usr/bin/env python3
"""Compatibility wrapper for the published, explicit ChatterPal setup commands."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chatterpal.cli.setup import main


if __name__ == "__main__":
    raise SystemExit(main())
